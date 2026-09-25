"""Durable camera-PC event ingestion for always-on bag analytics.

HTTP is deliberately performed before a database transaction.  Each returned
page is then committed as one unit: imported event rows, both CRM aggregates,
and the high-water cursor either all advance or all roll back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone

from . import ai, analytics, color_resolution, production_runs
from .event_policy import decide_event
from .event_protocol import (
    EVENT_PAGE_LIMIT,
    CountEvent,
    EventPage,
    EventSyncError,
    applies_to_continuous_analytics,
    brand_key,
    event_color_key,
    parse_page,
)
from .models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnStockBatch,
    ContinuousCameraRole,
)

EVENT_MAX_PAGES_PER_SYNC = 4
# Camera-PC facts of one event. A replayed event must match its imported row
# on every one of them; verification votes are informational and not compared.
EVENT_CONTENT_FIELDS = (
    "occurred_at",
    "source",
    "mode",
    "continuous_analytics",
    "analytics_scope",
    "class_name",
    "color",
    "color_confidence",
    "brand",
    "brand_confidence",
    "sku",
    "classification_status",
    "total_after",
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    processed: int
    ignored: int
    pages: int
    last_event_id: int | None
    caught_up: bool


@transaction.atomic
def mark_sync_failure(camera: str, error: Exception) -> None:
    """Persist a fail-closed journal error so it is not only a log line."""

    cursor = AlwaysOnCounterCursor.locked(ai.normalize(camera))
    cursor.event_sync_error = (str(error) or error.__class__.__name__)[:500]
    cursor.event_sync_failed_at = timezone.now()
    cursor.event_caught_up_at = None
    cursor.event_delivered_at = None
    cursor.event_sync_supported = (
        True
        if cursor.event_sync_supported is True or cursor.last_event_id is not None
        else None
    )
    cursor.save(
        update_fields=[
            "event_sync_error",
            "event_sync_failed_at",
            "event_caught_up_at",
            "event_delivered_at",
            "event_sync_supported",
            "updated_at",
        ]
    )


@transaction.atomic
def request_stop_drain(camera: str) -> None:
    """Persist removal intent before the remote processor is stopped."""

    cursor = AlwaysOnCounterCursor.locked(ai.normalize(camera))
    if cursor.event_sync_supported is not False:
        cursor.event_stop_drain_requested_at = timezone.now()
        cursor.event_stop_confirmed_at = None
        cursor.event_caught_up_at = None
        cursor.event_delivered_at = None
        cursor.save(
            update_fields=[
                "event_stop_drain_requested_at",
                "event_stop_confirmed_at",
                "event_caught_up_at",
                "event_delivered_at",
                "updated_at",
            ]
        )


@transaction.atomic
def confirm_stop_drain(camera: str) -> None:
    """Fence the final GET after a remote stop has been observed complete."""

    cursor = AlwaysOnCounterCursor.locked(ai.normalize(camera))
    if (
        cursor.event_sync_supported is not False
        and cursor.event_stop_drain_requested_at is not None
    ):
        confirmed_at = timezone.now()
        cursor.event_stop_confirmed_at = confirmed_at
        cursor.event_drain_required_at = confirmed_at
        cursor.event_caught_up_at = None
        cursor.event_delivered_at = None
        cursor.save(
            update_fields=[
                "event_stop_confirmed_at",
                "event_drain_required_at",
                "event_caught_up_at",
                "event_delivered_at",
                "updated_at",
            ]
        )


@transaction.atomic
def reactivate_stop_drain(
    camera: str,
    *,
    required_at: datetime | None = None,
) -> bool:
    """Replace an unconfirmed stale stop intent with a fresh live fence.

    A policy PUT can fail after ``request_stop_drain`` was committed. If the
    camera is assigned again before a later stop succeeds, that old intent can
    never be confirmed. Clear it only after reconcile has observed the camera
    in the desired live set, and require a poll that starts after this fence.
    """

    cursor = (
        AlwaysOnCounterCursor.objects.select_for_update()
        .filter(
            camera=ai.normalize(camera),
            event_stop_drain_requested_at__isnull=False,
            event_stop_confirmed_at__isnull=True,
        )
        .first()
    )
    if cursor is None:
        return False
    required_at = required_at or timezone.now()
    cursor.event_stop_drain_requested_at = None
    cursor.event_stop_confirmed_at = None
    cursor.event_drain_required_at = required_at
    cursor.event_caught_up_at = None
    cursor.event_delivered_at = None
    cursor.save(
        update_fields=[
            "event_stop_drain_requested_at",
            "event_stop_confirmed_at",
            "event_drain_required_at",
            "event_caught_up_at",
            "event_delivered_at",
            "updated_at",
        ]
    )
    return True


@transaction.atomic
def _mark_events_observed(camera: str) -> None:
    """Record the first successful /events reply as a one-way cutover."""

    cursor = AlwaysOnCounterCursor.locked(camera)
    cursor.event_sync_supported = True
    cursor.save(update_fields=["event_sync_supported", "updated_at"])


def _locked_accounting_periods(
    camera: str, events: tuple[CountEvent, ...]
) -> tuple[set[date], set[date]]:
    """Read shift/day guards once under the page's camera-cursor lock.

    Stock closing and archive operations acquire that same cursor first. Their
    eligibility cannot change until this page commits; no per-bag requery is
    needed. Keep row locks on existing periods and deterministic lock order.
    """
    ai_events = [
        event for event in events if event.analytics_scope == ANALYTICS_SCOPE_AI247
    ]
    business_days = {
        production_runs.business_day_for(event.occurred_at) for event in ai_events
    }
    calendar_days = {timezone.localdate(event.occurred_at) for event in ai_events}
    batches = (
        AlwaysOnStockBatch.objects.select_for_update()
        .filter(
            camera=camera,
            business_day__in=business_days,
        )
        .order_by("business_day")
    )
    days = (
        AlwaysOnDailyAnalytics.objects.select_for_update()
        .filter(
            camera=camera,
            day__in=calendar_days,
        )
        .order_by("day")
    )
    return (
        {
            row.business_day
            for row in batches
            if row.status in production_runs.TERMINAL_BATCH_STATUSES
        },
        {row.day for row in days if row.archived_at is not None},
    )


@transaction.atomic
def apply_page(
    *,
    camera: str,
    page: EventPage,
    requested_after_id: int,
    synced_at: datetime | None = None,
) -> tuple[int, int, int]:
    """Apply one validated page and return processed, ignored, cursor id."""

    synced_at = synced_at or timezone.now()
    cursor = AlwaysOnCounterCursor.locked(camera)
    current_id = cursor.last_event_id
    if current_id is None:
        current_id = 0
    if current_id < requested_after_id:
        raise EventSyncError("AI /events: database cursor moved backwards")
    if current_id != requested_after_id:
        # Another monitor committed a page while this HTTP request was in
        # flight.  Its cursor and caught-up marker are newer than this reply;
        # leave both untouched and make the caller fetch again from that
        # committed high-water mark.
        return 0, 0, current_id
    if cursor.event_journal_id is not None:
        if page.journal_id != cursor.event_journal_id:
            raise EventSyncError("AI /events: journal identity changed or disappeared")
    elif page.journal_id is not None:
        if current_id > 0:
            # Production initially exposed durable rows without an epoch. A
            # later identity could belong to that SQLite after an upgrade or
            # to a new empty database whose IDs restarted at one. Never guess:
            # continuity must be verified before an operator binds it.
            raise EventSyncError(
                "AI /events: journal identity appeared after cutover; "
                "manual continuity verification is required"
            )
        cursor.event_journal_id = page.journal_id
    processed = 0
    ignored = 0
    late_for_posted_shift = 0
    last_event_at = cursor.last_event_at
    role = (
        ContinuousCameraRole.objects.select_for_update()
        .filter(camera=camera)
        .values_list("analytics_scope", flat=True)
        .first()
    )
    if role is None:
        raise EventSyncError("AI /events: camera has no analytics role reservation")

    if not cursor.event_boundary_validated:
        first_continuous = next(
            (event for event in page.events if applies_to_continuous_analytics(event)),
            None,
        )
        if first_continuous is not None:
            if first_continuous.total_after < 1:
                raise EventSyncError("AI /events: invalid initial counter boundary")
            active_rows = list(
                analytics.active_daily_rows(
                    first_continuous.analytics_scope,
                    camera=camera,
                    day=timezone.localdate(first_continuous.occurred_at),
                ).select_for_update()
            )
            active_model_total = sum(row.model_total for row in active_rows)
            upstream_baseline = first_continuous.total_after - 1
            snapshot_to_shipping_reset = (
                first_continuous.analytics_scope == ANALYTICS_SCOPE_SHIPPING
                and cursor.last_total > 0
                and active_model_total == 0
                and upstream_baseline == 0
            )
            if snapshot_to_shipping_reset:
                raise EventSyncError(
                    "AI /events: shipping generation reset is not authorized"
                )
            if upstream_baseline not in {cursor.last_total, active_model_total}:
                raise EventSyncError(
                    "AI /events: initial counter boundary does not match CRM"
                )
            cursor.event_boundary_validated = True
        elif not page.has_more and not page.enrichment_pending:
            # A validated empty/ignored tail is itself a clean cutover point:
            # all historical aggregate counts remain in CRM and only future
            # journal events will be added.
            cursor.event_boundary_validated = True

    incoming = tuple(
        event for event in page.events if event.upstream_event_id > current_id
    )
    posted_days, archived_days = _locked_accounting_periods(camera, incoming)
    existing_events = {
        row.upstream_event_id: row
        for row in AlwaysOnImportedEvent.objects.filter(
            camera=camera,
            upstream_event_id__in=[event.upstream_event_id for event in incoming],
        )
    }
    new_events: list[AlwaysOnImportedEvent] = []
    first_resolvable_at: datetime | None = None

    for event in incoming:
        disposition = decide_event(event, role=role)
        applies_to_analytics = disposition.analytics
        applies_to_production = disposition.production
        if (
            applies_to_production
            and production_runs.business_day_for(event.occurred_at) in posted_days
        ):
            # The shift is already posted to stock, so this late bag (a
            # restart-gap backfill, typically) cannot join it. Refusing the
            # page would freeze the journal for every later event; instead the
            # day's analytics keep the count and the imported row records that
            # production never received it.
            applies_to_production = False
            late_for_posted_shift += 1
        imported = existing_events.get(event.upstream_event_id)
        if imported is not None:
            if any(
                getattr(imported, field) != getattr(event, field)
                for field in EVENT_CONTENT_FIELDS
            ):
                raise EventSyncError("AI /events: replayed event changed contents")
            if imported.applied_to_analytics != applies_to_analytics:
                raise EventSyncError("AI /events: imported event was not fully applied")
            if imported.applied_to_production != applies_to_production:
                raise EventSyncError("AI /events: event production eligibility changed")
        else:
            resolvable = (
                applies_to_analytics and event.analytics_scope == ANALYTICS_SCOPE_AI247
            )
            new_events.append(
                AlwaysOnImportedEvent(
                    camera=camera,
                    upstream_event_id=event.upstream_event_id,
                    verification_votes=event.verification_votes,
                    applied_to_analytics=applies_to_analytics,
                    applied_to_production=applies_to_production,
                    **{field: getattr(event, field) for field in EVENT_CONTENT_FIELDS},
                    **(
                        color_resolution.initial_markers(
                            event.color,
                            event.class_name,
                            event.brand,
                            event.classification_status,
                        )
                        if resolvable
                        else {}
                    ),
                )
            )
            if resolvable:
                first_resolvable_at = min(
                    filter(None, (first_resolvable_at, event.occurred_at))
                )
            if applies_to_analytics:
                if (
                    event.analytics_scope == ANALYTICS_SCOPE_AI247
                    and timezone.localdate(event.occurred_at) in archived_days
                ):
                    raise EventSyncError(
                        "AI /events: event belongs to an archived analytics day"
                    )
                analytics.record_counted_bag(
                    camera=camera,
                    color=event_color_key(event.color, event.class_name),
                    brand=brand_key(event.brand),
                    observed_at=event.occurred_at,
                    analytics_scope=event.analytics_scope,
                    record_production=applies_to_production,
                )
                processed += 1
            else:
                ignored += 1

        current_id = event.upstream_event_id
        last_event_at = event.occurred_at

    # Constraints remain authoritative. A conflicting writer or failed bulk
    # insert rolls back projections and cursor along with the entire page.
    AlwaysOnImportedEvent.objects.bulk_create(new_events, batch_size=EVENT_PAGE_LIMIT)
    if first_resolvable_at is not None:
        _resolve_unknown_bags(camera, first_resolvable_at)

    if late_for_posted_shift:
        log.warning(
            "Camera events arrived after their shift was posted camera=%s "
            "count=%s: counted in daily analytics only, not in production",
            camera,
            late_for_posted_shift,
        )

    # An empty first page is still a successful event-mode cutover.  Preserve
    # a concurrently advanced cursor rather than ever moving it backwards.
    cursor.last_event_id = max(current_id, requested_after_id)
    cursor.last_event_at = last_event_at
    ordinary_drain_satisfied = (
        cursor.event_drain_required_at is None
        or synced_at >= cursor.event_drain_required_at
    )
    stop_drain_satisfied = cursor.event_stop_drain_requested_at is None or (
        cursor.event_stop_confirmed_at is not None
        and synced_at >= cursor.event_stop_confirmed_at
    )
    drain_satisfied = ordinary_drain_satisfied and stop_drain_satisfied
    stream_caught_up = not page.has_more and not page.enrichment_pending
    cursor.event_caught_up_at = (
        synced_at if stream_caught_up and drain_satisfied else None
    )
    cursor.event_delivered_at = (
        synced_at if not page.has_more and drain_satisfied else None
    )
    if stream_caught_up and drain_satisfied:
        cursor.event_drain_required_at = None
        cursor.event_stop_drain_requested_at = None
        cursor.event_stop_confirmed_at = None
    cursor.event_sync_supported = True
    cursor.event_sync_error = ""
    cursor.event_sync_failed_at = None
    cursor.save(
        update_fields=[
            "last_event_id",
            "event_journal_id",
            "last_event_at",
            "event_caught_up_at",
            "event_delivered_at",
            "event_drain_required_at",
            "event_stop_drain_requested_at",
            "event_stop_confirmed_at",
            "event_sync_supported",
            "event_boundary_validated",
            "event_sync_error",
            "event_sync_failed_at",
            "updated_at",
        ]
    )
    return processed, ignored, cursor.last_event_id


def _resolve_unknown_bags(camera: str, since: datetime) -> None:
    """Best-effort neighbour pass; it must never freeze the event journal.

    A failure rolls back only its savepoint. The authoritative pass runs again
    over the whole shift right before stock posting.
    """

    try:
        with transaction.atomic():
            color_resolution.resolve_after_import(camera, since)
    except Exception:
        log.exception("AI 24/7 unknown-bag resolution failed camera=%s", camera)


def sync_camera(
    camera: str,
    *,
    max_pages: int = EVENT_MAX_PAGES_PER_SYNC,
) -> SyncResult:
    """Fetch and commit bounded pages for one camera without holding DB locks."""

    camera = ai.normalize(camera)
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    stored = (
        AlwaysOnCounterCursor.objects.filter(camera=camera)
        .only("last_event_id", "event_sync_supported")
        .first()
    )
    after_id = (
        stored.last_event_id if stored and stored.last_event_id is not None else 0
    )
    # The capability flag is one-way; once stored, do not re-lock the cursor
    # on every poll just to write the same True again.
    events_observed = stored is not None and stored.event_sync_supported is True
    processed = 0
    ignored = 0

    for page_number in range(1, max_pages + 1):
        # Store when this poll began, not when a delayed DB transaction later
        # commits.  The warehouse-close barrier can therefore require a poll
        # that definitely started after its cutoff and grace period.
        requested_at = timezone.now()
        payload = ai.count_events(camera, after_id, EVENT_PAGE_LIMIT)
        if not events_observed:
            _mark_events_observed(camera)
            events_observed = True
        page = parse_page(payload, camera=camera, after_id=after_id)
        added, skipped, cursor_id = apply_page(
            camera=camera,
            page=page,
            requested_after_id=after_id,
            synced_at=requested_at,
        )
        processed += added
        ignored += skipped
        if cursor_id != page.next_after_id:
            # A concurrent importer won the cursor lock.  Its state is
            # authoritative; this reply may no longer describe the stream
            # tail, so query again rather than claiming caught-up.
            after_id = cursor_id
            continue
        if not page.has_more:
            return SyncResult(
                processed,
                ignored,
                page_number,
                cursor_id,
                not page.enrichment_pending,
            )
        if cursor_id <= after_id:
            raise EventSyncError("AI /events cursor did not advance")
        after_id = cursor_id

    return SyncResult(
        processed,
        ignored,
        max_pages,
        after_id,
        False,
    )
