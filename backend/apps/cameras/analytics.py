from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import F, Q, Sum
from django.db.models.functions import Greatest
from django.utils import timezone

from . import color_resolution
from .models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    MonoblockCameraSettings,
    ShippingDailyAnalytics,
)
from .production_runs import record_color_event

EVENT_ANALYTICS_STALE_AGE = timedelta(seconds=90)
LEGACY_BRAND = "unclassified"


def daily_model_for(analytics_scope: str):
    if analytics_scope == ANALYTICS_SCOPE_AI247:
        return AlwaysOnDailyAnalytics
    if analytics_scope == ANALYTICS_SCOPE_SHIPPING:
        return ShippingDailyAnalytics
    raise ValueError("unknown continuous analytics scope")


def active_daily_rows(analytics_scope: str, **filters):
    """Дневные строки контура, входящие в текущий счёт.

    Архивные дни AI 24/7 остаются в базе ради истории, но их мешки уже
    перенесены в архив.
    """
    if analytics_scope == ANALYTICS_SCOPE_AI247:
        filters["archived_at__isnull"] = True
    return daily_model_for(analytics_scope).objects.filter(**filters)


def add_counts(base: dict | None, delta: dict) -> dict[str, int]:
    """Новая разбивка: к счётчикам ``base`` прибавлены счётчики ``delta``."""
    merged = dict(base or {})
    for key, value in delta.items():
        merged[key] = int(merged.get(key, 0)) + int(value)
    return merged


def _scope_sources(analytics_scope: str) -> list[str]:
    if analytics_scope == ANALYTICS_SCOPE_AI247:
        return MonoblockCameraSettings.ai247_sources()
    if analytics_scope == ANALYTICS_SCOPE_SHIPPING:
        return MonoblockCameraSettings.shipping_sources()
    raise ValueError("unknown continuous analytics scope")


def record_counted_bag(
    *,
    camera: str,
    color: str,
    brand: str | None,
    observed_at: datetime,
    analytics_scope: str,
    record_production: bool = True,
) -> None:
    """Apply one counted journal bag to both CRM ledgers.

    The caller must already be inside the transaction that owns the camera
    cursor.  ``color`` is "" for a bag the camera did not classify; ``brand``
    is ``None`` when enrichment is missing, which is kept separate from the
    classifier's explicit ``unknown`` so it is never shown as a prediction.
    """

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("camera count delta requires an atomic transaction")

    # Only the explicit AI 24/7 contour represents production and can create
    # warehouse receipts. Shipping analytics is an operational camera ledger.
    if analytics_scope == ANALYTICS_SCOPE_AI247 and record_production:
        record_color_event(camera, color, observed_at)

    row, _ = (
        daily_model_for(analytics_scope)
        .objects.select_for_update()
        .get_or_create(
            camera=camera,
            day=timezone.localdate(observed_at),
        )
    )
    row.model_total += 1
    if color:
        row.model_per_color = add_counts(row.model_per_color, {color: 1})
    row.model_per_brand = add_counts(row.model_per_brand, {brand or LEGACY_BRAND: 1})
    row.save(
        update_fields=[
            "model_total",
            "model_per_color",
            "model_per_brand",
            "updated_at",
        ]
    )


def _row_payload(
    row: AlwaysOnDailyAnalytics | ShippingDailyAnalytics | None,
    camera: str,
    day: date,
    analytics_scope: str = ANALYTICS_SCOPE_AI247,
) -> dict:
    colors = _normalized_colors(row.model_per_color if row else None)
    return {
        "camera": camera,
        "analytics_scope": analytics_scope,
        "day": day.isoformat(),
        "model_total": row.model_total if row else 0,
        "model_per_color": colors,
        # Готовая разбивка за день с процентами — её показывает клик по
        # столбику, и считается она там же, где общая, чтобы цифры сходились.
        "colors": color_payload(colors),
        "adjustment": row.adjustment if row else 0,
        "total": row.total if row else 0,
        "updated_at": row.updated_at if row else None,
    }


def _normalized_colors(raw) -> dict[str, int]:
    """Отбросить мусорные значения из сохранённой разбивки по цветам."""
    result: dict[str, int] = {}
    for color, value in (raw or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        result[color] = result.get(color, 0) + max(0, int(value))
    return result


def _merge_colors(rows) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        result = add_counts(result, _normalized_colors(row.model_per_color))
    return result


def apportion(counts: dict[str, int], total: int) -> dict[str, int]:
    """Split ``total`` in proportion to ``counts`` so the parts add up exactly.

    Метод наибольших остатков: целые части, затем остаток раздаём тем, у кого
    отброшенная дробная часть больше; при равенстве — более крупной части,
    затем в порядке ``counts``. ``counts`` должен быть с ненулевой суммой.
    """
    counted = sum(counts.values())
    exact = {name: value * total / counted for name, value in counts.items()}
    parts = {name: int(share) for name, share in exact.items()}
    order = sorted(counts, key=lambda name: (parts[name] - exact[name], -counts[name]))
    for name in order[: total - sum(parts.values())]:
        parts[name] += 1
    return parts


def _breakdown_payload(counts: dict[str, int], key: str) -> list[dict]:
    """Return exact one-decimal shares that add up to 100%.

    Округление каждой доли по отдельности давало 72.3 + 21.3 + 6.5 = 100.1%.
    Считаем в целых десятых процента (1000 = 100.0%) методом наибольших
    остатков, поэтому сумма сходится.
    """
    rows = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if not sum(counts.values()):
        return [{key: name, "total": value, "percent": 0} for name, value in rows]
    tenths = apportion(dict(rows), 1000)
    return [{key: name, "total": value, "percent": tenths[name] / 10} for name, value in rows]


def color_payload(colors: dict[str, int]) -> list[dict]:
    return _breakdown_payload(colors, "color")


def _history_payload(
    rows_by_day: dict[
        date,
        AlwaysOnDailyAnalytics | ShippingDailyAnalytics,
    ],
    start: date,
    end: date,
    analytics_scope: str,
) -> list[dict]:
    result = []
    current = start
    while current <= end:
        result.append(
            _row_payload(
                rows_by_day.get(current),
                "",
                current,
                analytics_scope,
            )
            | {"camera": None}
        )
        current += timedelta(days=1)
    return result


# Код AlwaysOnCounterCursor.sync_status → статус и текст для экрана аналитики.
_EVENT_SYNC_STATES = {
    "pending": ("pending", "Журнал событий камеры ещё не проверен"),
    "error": ("error", "Синхронизация событий завершилась ошибкой"),
    "unsupported": ("unsupported", "AI-сервис не поддерживает надёжный журнал событий"),
    "boundary": ("pending", "Начальная граница журнала событий ещё не подтверждена"),
    "catching_up": ("catching_up", "События камеры ещё загружаются"),
    "stale": ("stale", "Аналитика камеры давно не синхронизировалась"),
    "synced": ("synced", ""),
}


def _event_sync_payload(
    cursor: AlwaysOnCounterCursor | None,
    *,
    now: datetime,
) -> dict:
    code = (
        cursor.sync_status(now=now, max_age=EVENT_ANALYTICS_STALE_AGE)
        if cursor is not None
        else "pending"
    )
    status, detail = _EVENT_SYNC_STATES[code]
    if code == "error":
        detail = cursor.event_sync_error or detail
    return {
        "status": status,
        "available": status == "synced",
        "caught_up_at": cursor.event_caught_up_at if cursor else None,
        "last_event_at": cursor.last_event_at if cursor else None,
        "error": cursor.event_sync_error if cursor else "",
        "detail": detail,
    }


def _aggregate_sync_payload(rows: list[dict]) -> dict:
    unavailable = [row for row in rows if not row["available"]]
    if not unavailable:
        return {"status": "synced", "available": True, "detail": ""}
    priority = {
        "error": 0,
        "stale": 1,
        "catching_up": 2,
        "pending": 3,
        "unsupported": 4,
    }
    first = min(unavailable, key=lambda row: priority.get(row["status"], 99))
    return {
        "status": first["status"],
        "available": False,
        "detail": first["detail"],
    }


def _camera_payload(
    camera: str,
    rows: list[AlwaysOnDailyAnalytics | ShippingDailyAnalytics],
    *,
    day: date,
    history_start: date,
    history_end: date,
    ranged: bool,
    analytics_scope: str,
    all_time_total: int,
    inferred: dict[tuple[str, date], dict],
    analytics_sync: dict,
) -> dict:
    by_day = {row.day: row for row in rows}
    # Без периода цвета считаются за всю активную историю, с периодом —
    # только за выбранные дни (сегодняшняя строка нужна лишь для «сегодня»).
    period_rows = (
        [row for row in rows if history_start <= row.day <= history_end]
        if ranged
        else rows
    )
    colors = color_resolution.with_inferred(
        color_payload(_merge_colors(period_rows)),
        "color",
        [inferred.get((camera, row.day)) for row in period_rows],
    )
    return _row_payload(by_day.get(day), camera, day, analytics_scope) | {
        "all_time_total": all_time_total,
        **(
            {
                "date_from": history_start.isoformat(),
                "date_to": history_end.isoformat(),
                "period_total": sum(row.total for row in period_rows),
            }
            if ranged
            else {}
        ),
        "history": _history_payload(
            by_day,
            history_start,
            history_end,
            analytics_scope,
        ),
        "colors": colors,
        "analytics_sync": analytics_sync,
    }


def today_payload(
    analytics_scope: str = ANALYTICS_SCOPE_AI247,
    *,
    camera_sources: list[str] | tuple[str, ...] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Счёт камер контура за сегодня, за всё время и по дням периода.

    Камеры и период приходят уже проверенными AnalyticsRangeSerializer:
    период задан обоими концами или не задан вовсе (тогда 14 дней).
    """
    desired = _scope_sources(analytics_scope)
    if camera_sources is not None:
        requested = set(camera_sources)
        desired = [camera for camera in desired if camera in requested]
    day = timezone.localdate()
    ranged = date_from is not None
    history_start = date_from if ranged else day - timedelta(days=13)
    history_end = date_to if ranged else day
    now = timezone.now()
    # Cursor and daily totals are committed together by the event importer.
    # Read the cursor first: under READ COMMITTED a concurrent import can then
    # only expose an older cursor with newer rows (temporarily unavailable),
    # never newer authoritative sync state with older/empty rows (false zero).
    cursors = {
        row.camera: row
        for row in AlwaysOnCounterCursor.objects.filter(camera__in=desired)
    }
    queryset = active_daily_rows(analytics_scope, camera__in=desired)
    # Range reads load JSON breakdowns only for the selected days and today.
    # Lifetime counters use a grouped scalar aggregate, not all historical JSON.
    lifetime: dict[str, int] = {}
    if ranged:
        lifetime = dict(
            queryset.order_by()
            .values("camera")
            .annotate(total=Sum(Greatest(F("model_total") + F("adjustment"), 0)))
            .values_list("camera", "total")
        )
        queryset = queryset.filter(
            Q(day__range=(history_start, history_end)) | Q(day=day)
        )
    rows_by_camera: dict[
        str,
        list[AlwaysOnDailyAnalytics | ShippingDailyAnalytics],
    ] = {camera: [] for camera in desired}
    all_rows = list(queryset)
    for row in all_rows:
        rows_by_camera[row.camera].append(row)
    # Bags the camera left as ``unknown`` count under their resolved colour
    # (neighbours/votes/manual) exactly as in stock posting. The rows are only
    # read here; the stored ledger keeps the camera's own answers.
    inferred = (
        color_resolution.overlay_daily_rows(all_rows)
        if analytics_scope == ANALYTICS_SCOPE_AI247
        else {}
    )

    cameras = [
        _camera_payload(
            camera,
            camera_rows,
            day=day,
            history_start=history_start,
            history_end=history_end,
            ranged=ranged,
            analytics_scope=analytics_scope,
            all_time_total=lifetime.get(
                camera, sum(row.total for row in camera_rows)
            ),
            inferred=inferred,
            analytics_sync=_event_sync_payload(cursors.get(camera), now=now),
        )
        for camera, camera_rows in rows_by_camera.items()
    ]
    return {
        "analytics_scope": analytics_scope,
        "analytics_sync": _aggregate_sync_payload(
            [item["analytics_sync"] for item in cameras]
        ),
        "day": day.isoformat(),
        **(
            {
                "date_from": history_start.isoformat(),
                "date_to": history_end.isoformat(),
            }
            if ranged
            else {}
        ),
        "total": sum(item["total"] for item in cameras),
        "all_time_total": sum(item["all_time_total"] for item in cameras),
        "cameras": cameras,
    }
