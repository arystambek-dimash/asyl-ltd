"""Durable camera-PC event ingestion for always-on bag analytics.

HTTP is deliberately performed before a database transaction.  Each returned
page is then committed as one unit: imported event rows, both CRM aggregates,
and the high-water cursor either all advance or all roll back.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import ai, analytics, production
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

EVENT_PAGE_LIMIT = 500
EVENT_MAX_PAGES_PER_SYNC = 4


log = logging.getLogger(__name__)


class EventSyncError(Exception):
    """The upstream journal cannot be advanced without risking lost counts."""


@dataclass(frozen=True)
class CountEvent:
    upstream_event_id: int
    occurred_at: datetime
    camera: str
    source: str
    mode: str
    continuous_analytics: bool
    analytics_scope: str
    class_name: str
    total_after: int
    color: str | None = None
    color_confidence: float | None = None
    brand: str | None = None
    brand_confidence: float | None = None
    sku: str | None = None
    classification_status: str | None = None


@dataclass(frozen=True)
class EventPage:
    events: tuple[CountEvent, ...]
    next_after_id: int
    has_more: bool
    enrichment_pending: bool
    journal_id: str | None


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


def _plain_int(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EventSyncError(f"AI /events: invalid {field}")
    return value


def _optional_text(
    raw: dict,
    field: str,
    *,
    max_length: int,
) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise EventSyncError(f"AI /events: invalid event.{field}")
    return value


def _optional_confidence(raw: dict, field: str) -> float | None:
    value = raw.get(field)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise EventSyncError(f"AI /events: invalid event.{field}")
    return float(value)


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
    continuous_analytics = raw.get("continuous_analytics", False)
    if not isinstance(continuous_analytics, bool):
        raise EventSyncError("AI /events: invalid event.continuous_analytics")
    analytics_scope = raw.get("analytics_scope")
    if analytics_scope not in {ANALYTICS_SCOPE_SHIPPING, ANALYTICS_SCOPE_AI247}:
        raise EventSyncError("AI /events: invalid event.analytics_scope")
    source = raw.get("source")
    if source not in {"main", "sub"}:
        raise EventSyncError("AI /events: invalid event.source")
    if source != "sub" and (
        mode == "always_on" or continuous_analytics
    ):
        raise EventSyncError(
            "AI /events: continuous analytics event must use sub source"
        )
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
        continuous_analytics=continuous_analytics,
        analytics_scope=analytics_scope,
        class_name=class_name,
        total_after=total_after,
        color=_optional_text(raw, "color", max_length=100),
        color_confidence=_optional_confidence(raw, "color_confidence"),
        brand=_optional_text(raw, "brand", max_length=100),
        brand_confidence=_optional_confidence(raw, "brand_confidence"),
        sku=_optional_text(raw, "sku", max_length=255),
        classification_status=_optional_text(
            raw,
            "classification_status",
            max_length=32,
        ),
    )


def _applies_to_continuous_analytics(event: CountEvent) -> bool:
    """Honor the durable decision made when the camera event was created."""

    return event.mode == "always_on" or (
        event.mode == "session" and event.continuous_analytics
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
    enrichment_pending = payload.get("enrichment_pending", False)
    if not isinstance(enrichment_pending, bool):
        raise EventSyncError("AI /events: invalid enrichment_pending")
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
    return EventPage(
        tuple(events),
        next_after_id,
        has_more,
        enrichment_pending,
        journal_id,
    )


def _event_color(event: CountEvent) -> dict[str, int]:
    color = (event.color or event.class_name).split("_", 1)[0].strip().lower()
    return {color: 1} if color and len(color) <= 32 else {}


def _event_brand(event: CountEvent) -> dict[str, int] | None:
    """Return a classified brand, preserving absence as legacy data."""

    if event.brand is None:
        return None
    brand = " ".join(event.brand.split()).lower()
    return {brand: 1} if brand and len(brand) <= 100 else None


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


def _production_period_posted(event: CountEvent) -> bool:
    """True when the event's production shift is already posted to stock."""

    if event.analytics_scope != ANALYTICS_SCOPE_AI247:
        return False
    stock_batch = (
        AlwaysOnStockBatch.objects.select_for_update()
        .filter(
            camera=event.camera,
            business_day=production.business_day_for(event.occurred_at),
        )
        .first()
    )
    return (
        stock_batch is not None
        and stock_batch.status in production.TERMINAL_BATCH_STATUSES
    )


def _assert_open_accounting_period(
    event: CountEvent,
    *,
    record_production: bool,
) -> None:
    """Never mutate an already posted/archived period with a late event."""

    if event.analytics_scope == ANALYTICS_SCOPE_AI247:
        if record_production and _production_period_posted(event):
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
            raise EventSyncError(
                "AI /events: event belongs to an archived analytics day"
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

    for event in page.events:
        if event.upstream_event_id <= current_id:
            continue
        allowed_scope = event.analytics_scope == role
        bootstrap_ai_tail = bool(
            role == ANALYTICS_SCOPE_SHIPPING
            and pending_shipping_bootstrap
            and event.analytics_scope == ANALYTICS_SCOPE_AI247
        )
        if not allowed_scope and not bootstrap_ai_tail:
            raise EventSyncError(
                "AI /events: event analytics scope violates camera role"
            )
        legacy_ai_session = bool(
            role == ANALYTICS_SCOPE_AI247
            and event.mode == "session"
            and event.analytics_scope == ANALYTICS_SCOPE_AI247
        )
        # Old CV journals used the implicit AI scope for session rows.  A
        # permanently AI-owned camera imports those rows for cursor continuity
        # but never turns shipment crossings into AI production.  A pending
        # shipping bootstrap is the sole exception: it copies the delta only
        # into the rollback-owned daily baseline before the additive seed.
        applies_to_continuous = bool(
            _applies_to_continuous_analytics(event) and not legacy_ai_session
        )
        applies_to_shipping_bootstrap = bool(
            applies_to_continuous
            and bootstrap_ai_tail
        )
        applies_to_analytics = bool(
            applies_to_continuous and not applies_to_shipping_bootstrap
        )
        applies_to_production = bool(
            applies_to_analytics
            and event.analytics_scope == ANALYTICS_SCOPE_AI247
        )
        if applies_to_production and _production_period_posted(event):
            # The shift is already posted to stock, so this late bag (a
            # restart-gap backfill, typically) cannot join it. Refusing the
            # page would freeze the journal for every later event; instead the
            # day's analytics keep the count and the imported row records that
            # production never received it.
            applies_to_production = False
            late_for_posted_shift += 1
        applies_to_daily = applies_to_analytics or applies_to_shipping_bootstrap
        imported, created = AlwaysOnImportedEvent.objects.get_or_create(
            camera=camera,
            upstream_event_id=event.upstream_event_id,
            defaults={
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
            },
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
            if (
                imported.applied_to_shipping_bootstrap
                != applies_to_shipping_bootstrap
            ):
                raise EventSyncError("AI /events: event bootstrap eligibility changed")
        elif applies_to_daily:
            _assert_open_accounting_period(
                event,
                record_production=applies_to_production,
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
    stop_drain_satisfied = (
        cursor.event_stop_drain_requested_at is None
        or (
            cursor.event_stop_confirmed_at is not None
            and synced_at >= cursor.event_stop_confirmed_at
        )
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
