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

from . import ai, analytics, production
from .event_policy import decide_event
from .event_protocol import (
    EVENT_PAGE_LIMIT,
    CountEvent,
    EventPage,
    EventSyncError,
    _applies_to_continuous_analytics,
    _event_brand,
    _event_color,
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
    ShippingAnalyticsBootstrap,
    ShippingDailyAnalytics,
)

EVENT_MAX_PAGES_PER_SYNC = 4


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    supported: bool
    processed: int
    ignored: int
    pages: int
    last_event_id: int | None
    caught_up: bool


def _daily_model(analytics_scope: str):
    if analytics_scope == ANALYTICS_SCOPE_AI247:
        return AlwaysOnDailyAnalytics
    if analytics_scope == ANALYTICS_SCOPE_SHIPPING:
        return ShippingDailyAnalytics
    raise EventSyncError("AI /events: invalid event.analytics_scope")


@transaction.atomic
def mark_sync_failure(camera: str, error: Exception) -> None:
    """Persist a fail-closed journal error so it is not only a log line."""

    cursor, _ = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=ai.normalize(camera)
    )
    cursor.event_sync_error = (str(error) or error.__class__.__name__)[:500]
    cursor.event_sync_failed_at = timezone.now()
    cursor.event_caught_up_at = None
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
            "event_sync_supported",
            "updated_at",
        ]
    )


@transaction.atomic
def require_fresh_drain(
    camera: str, *, required_at: datetime | None = None
) -> datetime:
    """Invalidate older in-flight GETs before a stop/archive boundary."""

    required_at = required_at or timezone.now()
    cursor, _ = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=ai.normalize(camera)
    )
    if cursor.event_sync_supported is not False:
        cursor.event_drain_required_at = required_at
        cursor.event_caught_up_at = None
        cursor.save(
            update_fields=[
                "event_drain_required_at",
                "event_caught_up_at",
                "updated_at",
            ]
        )
    return required_at


@transaction.atomic
def request_stop_drain(camera: str) -> None:
    """Persist removal intent before the remote processor is stopped."""

    cursor, _ = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=ai.normalize(camera)
    )
    if cursor.event_sync_supported is not False:
        cursor.event_stop_drain_requested_at = timezone.now()
        cursor.event_stop_confirmed_at = None
        cursor.event_caught_up_at = None
        cursor.save(
            update_fields=[
                "event_stop_drain_requested_at",
                "event_stop_confirmed_at",
                "event_caught_up_at",
                "updated_at",
            ]
        )


@transaction.atomic
def confirm_stop_drain(camera: str) -> None:
    """Fence the final GET after a remote stop has been observed complete."""

    cursor, _ = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=ai.normalize(camera)
    )
    if (
        cursor.event_sync_supported is not False
        and cursor.event_stop_drain_requested_at is not None
    ):
        confirmed_at = timezone.now()
        cursor.event_stop_confirmed_at = confirmed_at
        cursor.event_drain_required_at = confirmed_at
        cursor.event_caught_up_at = None
        cursor.save(
            update_fields=[
                "event_stop_confirmed_at",
                "event_drain_required_at",
                "event_caught_up_at",
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
    cursor.save(
        update_fields=[
            "event_stop_drain_requested_at",
            "event_stop_confirmed_at",
            "event_drain_required_at",
            "event_caught_up_at",
            "updated_at",
        ]
    )
    return True


@transaction.atomic
def _mark_events_observed(camera: str) -> None:
    """Make a non-404 /events capability a permanent one-way decision."""

    cursor, _ = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=camera
    )
    cursor.event_sync_supported = True
    cursor.save(update_fields=["event_sync_supported", "updated_at"])


@transaction.atomic
def _mark_events_unsupported(camera: str) -> None:
    """Record an explicit legacy 404 without weakening an active cutover."""

    cursor, _ = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=camera
    )
    if cursor.last_event_id is not None or cursor.event_sync_supported is True:
        raise EventSyncError("AI /events disappeared after event-mode cutover")
    cursor.event_sync_supported = False
    cursor.event_sync_error = ""
    cursor.event_sync_failed_at = None
    cursor.event_caught_up_at = None
    cursor.event_drain_required_at = None
    cursor.event_stop_drain_requested_at = None
    cursor.event_stop_confirmed_at = None
    cursor.save(
        update_fields=[
            "event_sync_supported",
            "event_sync_error",
            "event_sync_failed_at",
            "event_caught_up_at",
            "event_drain_required_at",
            "event_stop_drain_requested_at",
            "event_stop_confirmed_at",
            "updated_at",
        ]
    )


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
        production.business_day_for(event.occurred_at) for event in ai_events
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
            if row.status in production.TERMINAL_BATCH_STATUSES
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
    cursor, _ = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=camera,
    )
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
    compat_total = (
        cursor.event_compat_total
        if cursor.event_compat_total is not None
        else cursor.last_total
    )
    compat_colors = dict(cursor.last_per_color or {})
    pending_shipping_bootstrap = (
        ShippingAnalyticsBootstrap.objects.select_for_update()
        .filter(camera=camera, completed_at__isnull=True)
        .exists()
    )
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
            (event for event in page.events if _applies_to_continuous_analytics(event)),
            None,
        )
        if first_continuous is not None:
            if first_continuous.total_after < 1:
                raise EventSyncError("AI /events: invalid initial counter boundary")
            daily_model = _daily_model(first_continuous.analytics_scope)
            daily_filters = {
                "camera": camera,
                "day": timezone.localdate(first_continuous.occurred_at),
            }
            if first_continuous.analytics_scope == ANALYTICS_SCOPE_AI247:
                daily_filters["archived_at__isnull"] = True
            active_rows = list(
                daily_model.objects.select_for_update().filter(**daily_filters)
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
                authorized_reset = (
                    ShippingAnalyticsBootstrap.objects.select_for_update()
                    .filter(
                        camera=camera,
                        scope_confirmed_at__isnull=False,
                        completed_at__isnull=True,
                    )
                    .exists()
                )
                if not authorized_reset:
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

    for event in page.events:
        if event.upstream_event_id <= current_id:
            continue
        disposition = decide_event(
            event,
            role=role,
            pending_shipping_bootstrap=pending_shipping_bootstrap,
        )
        applies_to_analytics = disposition.analytics
        applies_to_shipping_bootstrap = disposition.shipping_bootstrap
        applies_to_production = disposition.production
        if (
            applies_to_production
            and production.business_day_for(event.occurred_at) in posted_days
        ):
            # The shift is already posted to stock, so this late bag (a
            # restart-gap backfill, typically) cannot join it. Refusing the
            # page would freeze the journal for every later event; instead the
            # day's analytics keep the count and the imported row records that
            # production never received it.
            applies_to_production = False
            late_for_posted_shift += 1
        applies_to_daily = disposition.daily
        defaults = {
            "occurred_at": event.occurred_at,
            "source": event.source,
            "mode": event.mode,
            "continuous_analytics": event.continuous_analytics,
            "analytics_scope": event.analytics_scope,
            "class_name": event.class_name,
            "color": event.color,
            "color_confidence": event.color_confidence,
            "brand": event.brand,
            "brand_confidence": event.brand_confidence,
            "sku": event.sku,
            "classification_status": event.classification_status,
            "total_after": event.total_after,
            "applied_to_analytics": applies_to_analytics,
            "applied_to_production": applies_to_production,
            "applied_to_shipping_bootstrap": applies_to_shipping_bootstrap,
        }
        imported = existing_events.get(event.upstream_event_id)
        created = imported is None
        if created:
            new_events.append(
                AlwaysOnImportedEvent(
                    camera=camera,
                    upstream_event_id=event.upstream_event_id,
                    **defaults,
                )
            )
        if not created:
            if (
                imported.occurred_at != event.occurred_at
                or imported.source != event.source
                or imported.mode != event.mode
                or imported.continuous_analytics != event.continuous_analytics
                or imported.analytics_scope != event.analytics_scope
                or imported.class_name != event.class_name
                or imported.color != event.color
                or imported.color_confidence != event.color_confidence
                or imported.brand != event.brand
                or imported.brand_confidence != event.brand_confidence
                or imported.sku != event.sku
                or imported.classification_status != event.classification_status
                or imported.total_after != event.total_after
            ):
                raise EventSyncError("AI /events: replayed event changed contents")
            if imported.applied_to_analytics != applies_to_analytics:
                raise EventSyncError("AI /events: imported event was not fully applied")
            if imported.applied_to_production != applies_to_production:
                raise EventSyncError("AI /events: event production eligibility changed")
            if imported.applied_to_shipping_bootstrap != applies_to_shipping_bootstrap:
                raise EventSyncError("AI /events: event bootstrap eligibility changed")
        elif applies_to_daily:
            if (
                event.analytics_scope == ANALYTICS_SCOPE_AI247
                and timezone.localdate(event.occurred_at) in archived_days
            ):
                raise EventSyncError(
                    "AI /events: event belongs to an archived analytics day"
                )
            color_delta = _event_color(event)
            analytics.record_model_delta(
                camera=camera,
                color_delta=color_delta,
                brand_delta=_event_brand(event),
                total_delta=1,
                observed_at=event.occurred_at,
                analytics_scope=event.analytics_scope,
                ordered_color_event=True,
                record_production=applies_to_production,
            )
            compat_total += 1
            for color, value in color_delta.items():
                compat_colors[color] = int(compat_colors.get(color, 0)) + value
            processed += 1
        else:
            ignored += 1

        current_id = event.upstream_event_id
        last_event_at = event.occurred_at

    # Constraints remain authoritative. A conflicting writer or failed bulk
    # insert rolls back projections and cursor along with the entire page.
    AlwaysOnImportedEvent.objects.bulk_create(new_events, batch_size=EVENT_PAGE_LIMIT)

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
    if stream_caught_up and drain_satisfied:
        cursor.event_drain_required_at = None
        cursor.event_stop_drain_requested_at = None
        cursor.event_stop_confirmed_at = None
    cursor.event_sync_supported = True
    cursor.event_sync_error = ""
    cursor.event_sync_failed_at = None
    cursor.event_compat_total = compat_total
    cursor.last_total = compat_total
    cursor.last_per_color = compat_colors
    cursor.last_mode = "always_on"
    cursor.save(
        update_fields=[
            "last_event_id",
            "event_journal_id",
            "last_event_at",
            "event_caught_up_at",
            "event_drain_required_at",
            "event_stop_drain_requested_at",
            "event_stop_confirmed_at",
            "event_sync_supported",
            "event_boundary_validated",
            "event_sync_error",
            "event_sync_failed_at",
            "event_compat_total",
            "last_total",
            "last_per_color",
            "last_mode",
            "updated_at",
        ]
    )
    return processed, ignored, cursor.last_event_id


def sync_camera(
    camera: str,
    *,
    page_limit: int = EVENT_PAGE_LIMIT,
    max_pages: int = EVENT_MAX_PAGES_PER_SYNC,
) -> SyncResult:
    """Fetch and commit bounded pages for one camera without holding DB locks."""

    camera = ai.normalize(camera)
    if not 1 <= page_limit <= EVENT_PAGE_LIMIT:
        raise ValueError("page_limit must be between 1 and 500")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    stored = (
        AlwaysOnCounterCursor.objects.filter(camera=camera)
        .only("last_event_id")
        .first()
    )
    after_id = (
        stored.last_event_id if stored and stored.last_event_id is not None else 0
    )
    processed = 0
    ignored = 0

    for page_number in range(1, max_pages + 1):
        # Store when this poll began, not when a delayed DB transaction later
        # commits.  The warehouse-close barrier can therefore require a poll
        # that definitely started after its cutoff and grace period.
        requested_at = timezone.now()
        payload = ai.count_events(camera, after_id, page_limit)
        if payload is None:
            _mark_events_unsupported(camera)
            return SyncResult(False, 0, 0, 0, None, False)
        _mark_events_observed(camera)
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
                True,
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
        True,
        processed,
        ignored,
        max_pages,
        after_id,
        False,
    )
