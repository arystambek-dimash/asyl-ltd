"""Read-only shipping colour periods reconstructed from the durable count journal.

Daily aggregates are the coverage authority, never a source of invented event
times. Shipping has no warehouse runs: its periods are a display projection of
one-bag events and do not create production or stock records.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .analytics import EVENT_ANALYTICS_STALE_AGE
from .event_protocol import _event_color
from .models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    ShippingAnalyticsBootstrap,
    ShippingDailyAnalytics,
)
from .production_queries import _run_smoothing_payload
from .production_runs import RUN_GAP

MAX_DAY_EVENTS = 100_000
MAX_DAY_RUNS = 1_000


class HistoryUnavailable(Exception):
    """The selected evidence cannot produce a truthful, bounded day log."""


def _daily_colors(row) -> dict[str, int] | None:
    """Compare historical class labels and current base colours consistently."""

    values = row.model_per_color if row is not None else {}
    if not isinstance(values, dict):
        return None
    colors: Counter[str] = Counter()
    for key, count in values.items():
        if (
            not isinstance(key, str)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            return None
        color = key.split("_", 1)[0].strip().lower()
        if count and (not color or len(color) > 32):
            return None
        if count:
            colors[color] += count
    return dict(colors)


def _matches_daily(events: list[AlwaysOnImportedEvent], row) -> bool:
    colors: Counter[str] = Counter()
    for event in events:
        colors.update(_event_color(event))
    return len(events) == (row.model_total if row else 0) and dict(
        colors
    ) == _daily_colors(row)


def _runs(events: list[AlwaysOnImportedEvent], *, day: date, now: datetime, live: bool):
    """Keep authoritative event order; close on a colour change or a long gap."""

    zone = timezone.get_default_timezone()
    runs: list[dict] = []
    previous_at = None
    for event in events:
        observed = timezone.localtime(event.occurred_at, zone)
        # Imported IDs define sequence, but malformed clocks cannot define a
        # trustworthy time period. Do not reorder them to hide the conflict.
        if observed > now or (previous_at is not None and observed < previous_at):
            raise HistoryUnavailable(
                "В событиях камеры обнаружено несогласованное время. Периоды нельзя показать достоверно."
            )
        color = next(iter(_event_color(event)), "unclassified")
        if not runs or runs[-1]["color"] != color or observed - previous_at > RUN_GAP:
            if len(runs) >= MAX_DAY_RUNS:
                raise HistoryUnavailable(
                    "За день сохранено слишком много смен цвета для подробного журнала. Итоги доступны в аналитике."
                )
            runs.append(
                {
                    "id": event.pk,
                    "camera": event.camera,
                    "business_day": day.isoformat(),
                    "color": color,
                    "started_at": observed.isoformat(),
                    "last_counted_at": observed.isoformat(),
                    "ended_at": observed.isoformat(),
                    "model_bags": 1,
                    "is_approximate": False,
                    "status": "closed",
                }
            )
        else:
            runs[-1]["model_bags"] += 1
            runs[-1]["last_counted_at"] = observed.isoformat()
            runs[-1]["ended_at"] = observed.isoformat()
        previous_at = observed
    if runs and live and day == now.date() and now - previous_at <= RUN_GAP:
        runs[-1]["status"] = "active"
        runs[-1]["ended_at"] = None
    return runs


def day_payload(camera: str, *, day: date) -> dict:
    """Return exact periods, or explain why aggregate history has no exact log.

    Read the bootstrap fence and cursor before projections. Bounding the event
    query to that committed cursor keeps a concurrent importer from introducing
    future evidence. If the daily row advanced meanwhile, the final cursor
    check reports a retryable snapshot instead of a permanent coverage gap.
    No row locks or remote requests delay ingestion on this read path.
    """

    payload = {
        "camera": camera,
        "timezone": settings.TIME_ZONE,
        "selected_day": day.isoformat(),
        "day_runs": [],
        "algorithm_day_runs": [],
        "run_smoothing": _run_smoothing_payload([])[1],
        "history_status": "complete",
        "history_detail": "",
    }

    def unavailable(status: str, detail: str) -> dict:
        return {**payload, "history_status": status, "history_detail": detail}

    bootstrap = ShippingAnalyticsBootstrap.objects.filter(camera=camera).first()
    if bootstrap is not None and bootstrap.completed_at is None:
        return unavailable(
            "pending", "История камеры ещё синхронизируется. Повторите позже."
        )
    cursor = AlwaysOnCounterCursor.objects.filter(camera=camera).first()
    if cursor is not None and cursor.event_sync_supported is False:
        return unavailable(
            "incomplete",
            "Для этой камеры сохранены только итоговые счётчики. Журнал по времени недоступен.",
        )
    if (
        cursor is None
        or cursor.last_event_id is None
        or not cursor.event_boundary_validated
    ):
        return unavailable("pending", "Ожидаем синхронизацию журнала камеры.")

    row = ShippingDailyAnalytics.objects.filter(camera=camera, day=day).first()
    if row is not None and row.model_total > MAX_DAY_EVENTS:
        return unavailable(
            "incomplete",
            "За день сохранено слишком много событий для подробного журнала. Итоги доступны в аналитике.",
        )
    legacy = (
        AlwaysOnDailyAnalytics.objects.filter(
            camera=camera, day=day, archived_at__isnull=True
        ).first()
        if bootstrap is not None
        else None
    )
    start = timezone.make_aware(
        datetime.combine(day, time.min), timezone.get_default_timezone()
    )
    end = start + timedelta(days=1)
    eligible = Q(analytics_scope=ANALYTICS_SCOPE_SHIPPING, applied_to_analytics=True)
    if legacy is not None:
        eligible |= Q(
            Q(applied_to_analytics=True) | Q(applied_to_shipping_bootstrap=True),
            analytics_scope=ANALYTICS_SCOPE_AI247,
            imported_at__lte=bootstrap.completed_at,
        )
    events = list(
        AlwaysOnImportedEvent.objects.filter(
            eligible,
            Q(mode="always_on") | Q(mode="session", continuous_analytics=True),
            camera=camera,
            upstream_event_id__lte=cursor.last_event_id,
            occurred_at__gte=start,
            occurred_at__lt=end,
        ).only(
            "id",
            "camera",
            "upstream_event_id",
            "occurred_at",
            "color",
            "class_name",
            "analytics_scope",
        )[: MAX_DAY_EVENTS + 1]
    )
    if len(events) > MAX_DAY_EVENTS:
        return unavailable(
            "incomplete",
            "За день сохранено слишком много событий для подробного журнала. Итоги доступны в аналитике.",
        )
    legacy_events = [
        event for event in events if event.analytics_scope == ANALYTICS_SCOPE_AI247
    ]
    complete = _matches_daily(legacy_events, legacy) and _matches_daily(events, row)
    if not complete:
        latest_cursor = (
            AlwaysOnCounterCursor.objects.filter(camera=camera)
            .values_list("last_event_id", flat=True)
            .first()
        )
        if latest_cursor != cursor.last_event_id:
            return unavailable("pending", "Поступили новые данные. Журнал обновляется.")
        return unavailable(
            "incomplete",
            "Итог за день сохранён, но подробных событий недостаточно для полного журнала по времени.",
        )

    # Take the display clock after reading evidence: an import committed while
    # querying may legitimately contain a crossing newer than request start.
    now = timezone.localtime(timezone.now(), timezone.get_default_timezone())
    live = bool(
        cursor.event_sync_supported is True
        and cursor.event_caught_up_at is not None
        and timedelta(0) <= now - cursor.event_caught_up_at <= EVENT_ANALYTICS_STALE_AGE
        and not cursor.event_sync_error
        and cursor.event_sync_failed_at is None
        and cursor.event_drain_required_at is None
        and cursor.event_stop_drain_requested_at is None
    )
    try:
        raw_runs = _runs(events, day=day, now=now, live=live)
    except HistoryUnavailable as exc:
        return unavailable("incomplete", str(exc))
    algorithm_runs, metadata = _run_smoothing_payload(raw_runs)
    return {
        **payload,
        "day_runs": raw_runs,
        "algorithm_day_runs": algorithm_runs,
        "run_smoothing": metadata,
    }
