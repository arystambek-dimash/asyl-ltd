"""Durable camera-PC event ingestion for always-on bag analytics.

HTTP is deliberately performed before a database transaction.  Each returned
page is then committed as one unit: imported event rows, both CRM aggregates,
and the high-water cursor either all advance or all roll back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import ai, analytics, production
from .models import (
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnStockBatch,
)

EVENT_PAGE_LIMIT = 500
EVENT_MAX_PAGES_PER_SYNC = 4


class EventSyncError(Exception):
    """The upstream journal cannot be advanced without risking lost counts."""


@dataclass(frozen=True)
class CountEvent:
    upstream_event_id: int
    occurred_at: datetime
    camera: str
    source: str
    mode: str
    class_name: str
    total_after: int


@dataclass(frozen=True)
class EventPage:
    events: tuple[CountEvent, ...]
    next_after_id: int
    has_more: bool
    journal_id: str | None


@dataclass(frozen=True)
class SyncResult:
    supported: bool
    processed: int
    ignored: int
    pages: int
    last_event_id: int | None
    caught_up: bool


def _plain_int(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EventSyncError(f"AI /events: invalid {field}")
    return value


def _parse_event(raw: object, *, camera: str, previous_id: int) -> CountEvent:
    if not isinstance(raw, dict):
        raise EventSyncError("AI /events: event must be an object")
    event_id = _plain_int(raw.get("id"), "event.id", minimum=1)
    if event_id <= previous_id:
        raise EventSyncError("AI /events: event ids must be strictly increasing")
    if raw.get("cam") != camera:
        raise EventSyncError("AI /events: event camera does not match the filter")

    created_at = raw.get("created_at")
    occurred_at = parse_datetime(created_at) if isinstance(created_at, str) else None
    if occurred_at is None or timezone.is_naive(occurred_at):
        raise EventSyncError("AI /events: invalid event.created_at")

    mode = raw.get("mode")
    if mode not in {"always_on", "session"}:
        raise EventSyncError("AI /events: invalid event.mode")
    source = raw.get("source")
    if source not in {"main", "sub"}:
        raise EventSyncError("AI /events: invalid event.source")
    class_name = raw.get("class_name")
    if not isinstance(class_name, str) or len(class_name) > 100:
        raise EventSyncError("AI /events: invalid event.class_name")
    total_after = _plain_int(raw.get("total_after"), "event.total_after")
    return CountEvent(
        upstream_event_id=event_id,
        occurred_at=occurred_at,
        camera=camera,
        source=source,
        mode=mode,
        class_name=class_name,
        total_after=total_after,
    )


def parse_page(payload: object, *, camera: str, after_id: int) -> EventPage:
    """Validate the observed production /events contract without coercion."""

    if not isinstance(payload, dict):
        raise EventSyncError("AI /events: response must be an object")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or len(raw_events) > EVENT_PAGE_LIMIT:
        raise EventSyncError("AI /events: invalid events page")
    has_more = payload.get("has_more")
    if not isinstance(has_more, bool):
        raise EventSyncError("AI /events: invalid has_more")
    journal_id = payload.get("journal_id")
    if journal_id is not None and (
        not isinstance(journal_id, str)
        or not journal_id.strip()
        or len(journal_id) > 64
    ):
        raise EventSyncError("AI /events: invalid journal_id")

    events: list[CountEvent] = []
    previous_id = after_id
    for raw in raw_events:
        event = _parse_event(raw, camera=camera, previous_id=previous_id)
        events.append(event)
        previous_id = event.upstream_event_id

    next_after_id = _plain_int(payload.get("next_after_id"), "next_after_id")
    expected_next = events[-1].upstream_event_id if events else after_id
    if next_after_id != expected_next:
        raise EventSyncError("AI /events: next_after_id skipped an event")
    if has_more and not events:
        raise EventSyncError("AI /events: has_more without cursor progress")
    return EventPage(tuple(events), next_after_id, has_more, journal_id)


def _event_color(event: CountEvent) -> dict[str, int]:
    color = event.class_name.split("_", 1)[0].strip().lower()
    return {color: 1} if color and len(color) <= 32 else {}


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


def _assert_open_accounting_period(event: CountEvent) -> None:
    """Never mutate an already posted/archived period with a late event."""

    business_day = production.business_day_for(event.occurred_at)
    stock_batch = (
        AlwaysOnStockBatch.objects.select_for_update()
        .filter(camera=event.camera, business_day=business_day)
        .first()
    )
    if (
        stock_batch is not None
        and stock_batch.status in production.TERMINAL_BATCH_STATUSES
    ):
        raise EventSyncError(
            "AI /events: event belongs to an already posted production shift"
        )
    day = timezone.localdate(event.occurred_at)
    daily_row = (
        AlwaysOnDailyAnalytics.objects.select_for_update()
        .filter(camera=event.camera, day=day)
        .first()
    )
    if daily_row is not None and daily_row.archived_at is not None:
        raise EventSyncError("AI /events: event belongs to an archived analytics day")


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
    last_event_at = cursor.last_event_at
    compat_total = (
        cursor.event_compat_total
        if cursor.event_compat_total is not None
        else cursor.last_total
    )
    compat_colors = dict(cursor.last_per_color or {})

    if not cursor.event_boundary_validated:
        first_always_on = next(
            (event for event in page.events if event.mode == "always_on"),
            None,
        )
        if first_always_on is not None:
            if first_always_on.total_after < 1:
                raise EventSyncError("AI /events: invalid initial counter boundary")
            active_rows = list(
                AlwaysOnDailyAnalytics.objects.select_for_update().filter(
                    camera=camera,
                    day=timezone.localdate(first_always_on.occurred_at),
                    archived_at__isnull=True,
                )
            )
            active_model_total = sum(row.model_total for row in active_rows)
            upstream_baseline = first_always_on.total_after - 1
            if upstream_baseline not in {cursor.last_total, active_model_total}:
                raise EventSyncError(
                    "AI /events: initial counter boundary does not match CRM"
                )
            cursor.event_boundary_validated = True
        elif not page.has_more:
            # A validated empty/ignored tail is itself a clean cutover point:
            # all historical aggregate counts remain in CRM and only future
            # journal events will be added.
            cursor.event_boundary_validated = True

    for event in page.events:
        if event.upstream_event_id <= current_id:
            continue
        imported, created = AlwaysOnImportedEvent.objects.get_or_create(
            camera=camera,
            upstream_event_id=event.upstream_event_id,
            defaults={
                "occurred_at": event.occurred_at,
                "source": event.source,
                "mode": event.mode,
                "class_name": event.class_name,
                "total_after": event.total_after,
                "applied_to_analytics": event.mode == "always_on",
            },
        )
        if not created:
            if (
                imported.occurred_at != event.occurred_at
                or imported.source != event.source
                or imported.mode != event.mode
                or imported.class_name != event.class_name
                or imported.total_after != event.total_after
            ):
                raise EventSyncError("AI /events: replayed event changed contents")
            if imported.applied_to_analytics != (event.mode == "always_on"):
                raise EventSyncError("AI /events: imported event was not fully applied")
        elif event.mode == "always_on":
            _assert_open_accounting_period(event)
            color_delta = _event_color(event)
            analytics.record_model_delta(
                camera=camera,
                color_delta=color_delta,
                total_delta=1,
                observed_at=event.occurred_at,
                ordered_color_event=True,
            )
            compat_total += 1
            for color, value in color_delta.items():
                compat_colors[color] = int(compat_colors.get(color, 0)) + value
            processed += 1
        else:
            ignored += 1

        current_id = event.upstream_event_id
        last_event_at = event.occurred_at

    # An empty first page is still a successful event-mode cutover.  Preserve
    # a concurrently advanced cursor rather than ever moving it backwards.
    cursor.last_event_id = max(current_id, requested_after_id)
    cursor.last_event_at = last_event_at
    ordinary_drain_satisfied = (
        cursor.event_drain_required_at is None
        or synced_at >= cursor.event_drain_required_at
    )
    stop_drain_satisfied = (
        cursor.event_stop_drain_requested_at is None
        or (
            cursor.event_stop_confirmed_at is not None
            and synced_at >= cursor.event_stop_confirmed_at
        )
    )
    drain_satisfied = ordinary_drain_satisfied and stop_drain_satisfied
    cursor.event_caught_up_at = (
        synced_at if not page.has_more and drain_satisfied else None
    )
    if not page.has_more and drain_satisfied:
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
                True,
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
