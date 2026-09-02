from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from . import ai
from .models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCountArchive,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    ShippingAnalyticsBootstrap,
    ShippingDailyAnalytics,
)

EVENT_ARCHIVE_MAX_SYNC_AGE = timedelta(seconds=60)
EVENT_ANALYTICS_STALE_AGE = timedelta(seconds=90)
LEGACY_BRAND = "unclassified"
UNKNOWN_BRAND = "unknown"
NON_DOMINANT_BRANDS = frozenset({LEGACY_BRAND, UNKNOWN_BRAND})


def _assert_ai247_reservation(camera: str) -> str:
    camera = ai.normalize(camera)
    if not ContinuousCameraRole.objects.filter(
        camera=camera,
        analytics_scope=ANALYTICS_SCOPE_AI247,
    ).exists():
        raise ValidationError(
            {
                "detail": "Камера не закреплена за контуром AI 24/7",
                "code": "camera_not_in_ai247",
            }
        )
    return camera


def _daily_model(analytics_scope: str):
    if analytics_scope == ANALYTICS_SCOPE_AI247:
        return AlwaysOnDailyAnalytics
    if analytics_scope == ANALYTICS_SCOPE_SHIPPING:
        return ShippingDailyAnalytics
    raise ValueError("unknown continuous analytics scope")


@transaction.atomic
def confirm_shipping_bootstrap_scope(camera: str) -> bool:
    """Persist CV's role-aware acknowledgement before importing its tail."""

    marker = (
        ShippingAnalyticsBootstrap.objects.select_for_update()
        .filter(
            camera=ai.normalize(camera),
            completed_at__isnull=True,
        )
        .first()
    )
    if marker is None:
        return False
    if marker.scope_confirmed_at is None:
        marker.scope_confirmed_at = timezone.now()
        marker.save(update_fields=["scope_confirmed_at"])
    return True


@transaction.atomic
def complete_shipping_bootstrap(camera: str) -> bool:
    """Add the final legacy tail once the role-aware journal is caught up.

    This is additive because post-cutover shipping events may already exist in
    the new table. Legacy rows remain untouched for an automatic old-image
    rollback. The cursor lock serializes this transfer with event ingestion.
    """

    camera = ai.normalize(camera)
    if not ShippingAnalyticsBootstrap.objects.filter(
        camera=camera,
        completed_at__isnull=True,
    ).exists():
        return False
    cursor = (
        AlwaysOnCounterCursor.objects.select_for_update()
        .filter(camera=camera)
        .first()
    )
    marker = (
        ShippingAnalyticsBootstrap.objects.select_for_update()
        .filter(camera=camera, completed_at__isnull=True)
        .first()
    )
    if marker is None:
        return False
    if (
        cursor is None
        or cursor.event_sync_supported is not True
        or not cursor.event_boundary_validated
        or cursor.event_sync_error
        or cursor.event_sync_failed_at is not None
        or cursor.event_caught_up_at is None
        or cursor.event_caught_up_at < marker.created_at
        or marker.scope_confirmed_at is None
        or cursor.event_caught_up_at < marker.scope_confirmed_at
    ):
        return False

    legacy_rows = list(
        AlwaysOnDailyAnalytics.objects.select_for_update().filter(
            camera=camera,
            archived_at__isnull=True,
        )
    )
    for legacy in legacy_rows:
        shipping, _ = (
            ShippingDailyAnalytics.objects.select_for_update().get_or_create(
                camera=camera,
                day=legacy.day,
            )
        )
        colors = dict(shipping.model_per_color or {})
        for color, value in (legacy.model_per_color or {}).items():
            colors[color] = int(colors.get(color, 0)) + int(value)
        brands = dict(shipping.model_per_brand or {})
        for brand, value in (legacy.model_per_brand or {}).items():
            brands[brand] = int(brands.get(brand, 0)) + int(value)
        shipping.model_total += legacy.model_total
        shipping.model_per_color = colors
        shipping.model_per_brand = brands
        shipping.adjustment += legacy.adjustment
        shipping.save(
            update_fields=[
                "model_total",
                "model_per_color",
                "model_per_brand",
                "adjustment",
                "updated_at",
            ]
        )

    marker.completed_at = timezone.now()
    marker.save(update_fields=["completed_at"])
    return True


def _processor_total(processor: dict) -> int | None:
    value = processor.get("total")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        value = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _processor_colors(processor: dict) -> dict[str, int]:
    """Collapse model classes such as Red_50 and Blue_25 into base colours."""
    raw = processor.get("per_color")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or isinstance(value, bool):
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        color = key.split("_", 1)[0].strip().lower()
        if color:
            result[color] = result.get(color, 0) + value
    return result


_RESTART_TOTAL_MAX = 5


def _counter_delta(current: int, previous: int) -> int:
    if current >= previous:
        return current - previous
    return current if current <= _RESTART_TOTAL_MAX else 0


def _color_delta(current: dict[str, int], previous: dict) -> dict[str, int]:
    result = {}
    for color, value in current.items():
        old = previous.get(color, 0) if isinstance(previous, dict) else 0
        if isinstance(old, bool) or not isinstance(old, (int, float)):
            old = 0
        delta = _counter_delta(value, max(0, int(old)))
        if delta > 0:
            result[color] = delta
    return result


def _normalize_brand(value: object) -> str:
    if not isinstance(value, str):
        return LEGACY_BRAND
    brand = " ".join(value.split()).lower()
    return brand if brand and len(brand) <= 100 else LEGACY_BRAND


def _bounded_brand_delta(raw: dict[str, int] | None, total: int) -> dict[str, int]:
    """Fit a brand split to the authoritative bag total.

    Missing enrichment is kept separate from the classifier's explicit
    ``unknown`` result so legacy periods are never presented as a prediction.
    """

    remaining = max(0, int(total))
    result: dict[str, int] = {}
    for raw_brand, raw_bags in (raw or {}).items():
        if remaining <= 0 or isinstance(raw_bags, bool):
            continue
        try:
            bags = max(0, int(raw_bags))
        except (OverflowError, TypeError, ValueError):
            continue
        accepted = min(bags, remaining)
        if not accepted:
            continue
        brand = _normalize_brand(raw_brand)
        result[brand] = result.get(brand, 0) + accepted
        remaining -= accepted
    if remaining:
        result[LEGACY_BRAND] = result.get(LEGACY_BRAND, 0) + remaining
    return result


def record_model_delta(
    *,
    camera: str,
    color_delta: dict[str, int],
    brand_delta: dict[str, int] | None = None,
    total_delta: int,
    observed_at: datetime,
    analytics_scope: str = ANALYTICS_SCOPE_AI247,
    ordered_color_event: bool = False,
    record_production: bool = True,
) -> None:
    """Apply one authoritative count delta to both CRM ledgers.

    The caller must already be inside the transaction that owns the camera
    cursor.  Keeping this helper free of its own cursor decisions lets snapshot
    compatibility and the durable event importer share exactly the same CRM
    accounting path.
    """

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("camera count delta requires an atomic transaction")
    if total_delta <= 0:
        return

    daily_model = _daily_model(analytics_scope)

    # Only the explicit AI 24/7 contour represents production and can create
    # warehouse receipts. Shipping analytics is an operational camera ledger.
    if analytics_scope == ANALYTICS_SCOPE_AI247 and record_production:
        from . import production

        production.record_color_deltas(
            camera=camera,
            color_deltas=color_delta,
            total_delta=total_delta,
            observed_at=observed_at,
            ordered_color_event=ordered_color_event,
        )

    row, _ = daily_model.objects.select_for_update().get_or_create(
        camera=camera,
        day=timezone.localdate(observed_at),
    )
    merged_colors = dict(row.model_per_color or {})
    for color, value in color_delta.items():
        merged_colors[color] = int(merged_colors.get(color, 0)) + value
    merged_brands = dict(row.model_per_brand or {})
    for brand, value in _bounded_brand_delta(brand_delta, total_delta).items():
        merged_brands[brand] = int(merged_brands.get(brand, 0)) + value
    row.model_total += total_delta
    row.model_per_color = merged_colors
    row.model_per_brand = merged_brands
    row.save(
        update_fields=[
            "model_total",
            "model_per_color",
            "model_per_brand",
            "updated_at",
        ]
    )


@transaction.atomic
def _record_processor(
    processor: dict,
    observed_at: datetime,
    *,
    analytics_scope: str,
) -> None:
    camera = processor.get("cam")
    total = _processor_total(processor)
    if not isinstance(camera, str) or total is None:
        return
    try:
        camera = ai.normalize(camera)
    except ai.AiError:
        return
    colors = _processor_colors(processor)

    authoritative = processor.get("mode") == "always_on" and bool(
        processor.get("running")
    )
    if not authoritative:
        return

    cursor, created = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=camera,
        defaults={
            "last_total": total,
            "last_per_color": colors,
            "last_mode": str(processor.get("mode") or ""),
        },
    )
    # Event mode is a one-way cutover.  A timeout or an older cached snapshot
    # must never make the same physical crossing enter CRM for a second time.
    if cursor.last_event_id is not None:
        return
    if created:
        delta = total
        color_delta = colors
    else:
        delta = _counter_delta(total, cursor.last_total)
        color_delta = _color_delta(colors, cursor.last_per_color)

    rewind = not created and total < cursor.last_total
    if rewind and total > _RESTART_TOTAL_MAX:
        return
    cursor.last_total = total
    cursor.last_per_color = colors
    cursor.last_mode = str(processor.get("mode") or "")[:16]
    cursor.save(
        update_fields=["last_total", "last_per_color", "last_mode", "updated_at"]
    )

    if delta <= 0:
        return

    record_model_delta(
        camera=camera,
        color_delta=color_delta,
        total_delta=delta,
        observed_at=observed_at,
        analytics_scope=analytics_scope,
    )


def record_snapshot(
    live: dict,
    observed_at: datetime | None = None,
    *,
    cameras: set[str] | None = None,
    analytics_scopes: dict[str, str] | None = None,
) -> None:
    observed_at = observed_at or timezone.now()
    processors = live.get("processors") if isinstance(live, dict) else None
    if not isinstance(processors, list):
        return
    for processor in processors:
        if not isinstance(processor, dict):
            continue
        if cameras is not None:
            try:
                camera = ai.normalize(processor.get("cam"))
            except ai.AiError:
                continue
            if camera not in cameras:
                continue
        try:
            camera = ai.normalize(processor.get("cam"))
        except ai.AiError:
            continue
        if analytics_scopes is None:
            scope = ANALYTICS_SCOPE_AI247
        else:
            # A caller with an explicit role map must never guess the missing
            # camera into AI 24/7 (and accidentally create stock movements).
            scope = analytics_scopes.get(camera)
        if scope not in {ANALYTICS_SCOPE_AI247, ANALYTICS_SCOPE_SHIPPING}:
            continue
        _record_processor(
            processor,
            observed_at,
            analytics_scope=scope,
        )


def _row_payload(
    row: AlwaysOnDailyAnalytics | ShippingDailyAnalytics | None,
    camera: str,
    day: date,
    analytics_scope: str = ANALYTICS_SCOPE_AI247,
) -> dict:
    colors = _normalized_colors(row.model_per_color if row else None)
    model_total = row.model_total if row else 0
    brands = _normalized_brands(row.model_per_brand if row else None, model_total)
    return {
        "camera": camera,
        "analytics_scope": analytics_scope,
        "day": day.isoformat(),
        "model_total": model_total,
        "model_per_color": colors,
        "model_per_brand": brands,
        # Готовая разбивка за день с процентами — её показывает клик по
        # столбику, и считается она там же, где общая, чтобы цифры сходились.
        "colors": _color_payload(colors),
        "brands": _brand_payload(brands),
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
        for color, value in _normalized_colors(row.model_per_color).items():
            result[color] = result.get(color, 0) + value
    return result


def _normalized_brands(raw, model_total: int) -> dict[str, int]:
    return _bounded_brand_delta(raw if isinstance(raw, dict) else None, model_total)


def _merge_brands(rows) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        for brand, value in _normalized_brands(
            row.model_per_brand,
            row.model_total,
        ).items():
            result[brand] = result.get(brand, 0) + value
    return result


def _breakdown_payload(counts: dict[str, int], key: str) -> list[dict]:
    """Return exact one-decimal shares that add up to 100%.

    Округление каждой доли по отдельности давало 72.3 + 21.3 + 6.5 = 100.1%.
    Считаем в десятых долях процента и раздаём остаток по наибольшему
    дробному хвосту (метод наибольших остатков), поэтому сумма сходится.
    """
    total = sum(counts.values())
    rows = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if not total:
        return [{key: name, "total": value, "percent": 0} for name, value in rows]

    # Работаем в целых десятых процента: 1000 = 100.00%.
    exact = [value * 1000 / total for _color, value in rows]
    tenths = [int(share) for share in exact]
    remainder = 1000 - sum(tenths)
    # Остаток отдаём тем, у кого отброшенная часть больше — при равенстве
    # выигрывает более крупный цвет, порядок уже отсортирован по убыванию.
    order = sorted(range(len(rows)), key=lambda i: (-(exact[i] - tenths[i]), i))
    for i in order[:remainder]:
        tenths[i] += 1

    return [
        {key: name, "total": value, "percent": tenths[index] / 10}
        for index, (name, value) in enumerate(rows)
    ]


def _color_payload(colors: dict[str, int]) -> list[dict]:
    return _breakdown_payload(colors, "color")


def _brand_payload(brands: dict[str, int]) -> list[dict]:
    return _breakdown_payload(brands, "brand")


def _dominant_brand(items: list[dict]) -> str | None:
    return next(
        (
            item["brand"]
            for item in items
            if item["brand"] not in NON_DOMINANT_BRANDS
        ),
        None,
    )


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


def _event_sync_payload(
    cursor: AlwaysOnCounterCursor | None,
    *,
    now: datetime,
    bootstrap_pending: bool = False,
) -> dict:
    status = "pending"
    detail = "Журнал событий камеры ещё не проверен"
    if cursor is not None:
        if cursor.event_sync_error or cursor.event_sync_failed_at is not None:
            status = "error"
            detail = cursor.event_sync_error or "Синхронизация событий завершилась ошибкой"
        elif cursor.event_sync_supported is False:
            status = "unsupported"
            detail = "AI-сервис не поддерживает надёжный журнал событий"
        elif not cursor.event_boundary_validated:
            status = "pending"
            detail = "Начальная граница журнала событий ещё не подтверждена"
        elif cursor.event_caught_up_at is None:
            status = "catching_up"
            detail = "События камеры ещё загружаются"
        elif now - cursor.event_caught_up_at > EVENT_ANALYTICS_STALE_AGE:
            status = "stale"
            detail = "Аналитика камеры давно не синхронизировалась"
        else:
            status = "synced"
            detail = ""
    if bootstrap_pending and status == "synced":
        status = "catching_up"
        detail = "История отгрузки ещё переносится"
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


def today_payload(
    analytics_scope: str = ANALYTICS_SCOPE_AI247,
    *,
    camera_sources: list[str] | tuple[str, ...] | None = None,
) -> dict:
    if analytics_scope == ANALYTICS_SCOPE_AI247:
        desired = MonoblockCameraSettings.ai247_sources()
    elif analytics_scope == ANALYTICS_SCOPE_SHIPPING:
        desired = MonoblockCameraSettings.shipping_sources()
    else:
        raise ValueError("unknown continuous analytics scope")
    if camera_sources is not None:
        requested = {
            ai.normalize(camera)
            for camera in camera_sources
        }
        desired = [camera for camera in desired if camera in requested]
    day = timezone.localdate()
    history_start = day - timedelta(days=13)
    # Архивные дни остаются в базе ради истории, но в текущий счёт не входят:
    # их мешки уже посчитаны и перенесены в архив.
    daily_model = _daily_model(analytics_scope)
    daily_filters = {"camera__in": desired}
    if analytics_scope == ANALYTICS_SCOPE_AI247:
        daily_filters["archived_at__isnull"] = True
    # Read the one-time fence before rows. If bootstrap commits between these
    # reads, this response remains unavailable; if it committed earlier, its
    # atomic row updates are necessarily visible below. Never emit a transient
    # synced zero from a pre-seed snapshot.
    pending_bootstraps = (
        set(
            ShippingAnalyticsBootstrap.objects.filter(
                camera__in=desired,
                completed_at__isnull=True,
            ).values_list("camera", flat=True)
        )
        if analytics_scope == ANALYTICS_SCOPE_SHIPPING
        else set()
    )
    now = timezone.now()
    # Cursor and daily totals are committed together by the event importer.
    # Read the cursor first: under READ COMMITTED a concurrent import can then
    # only expose an older cursor with newer rows (temporarily unavailable),
    # never newer authoritative sync state with older/empty rows (false zero).
    cursors = {
        row.camera: row
        for row in AlwaysOnCounterCursor.objects.filter(camera__in=desired)
    }
    all_rows = list(daily_model.objects.filter(**daily_filters))
    rows_by_camera: dict[
        str,
        list[AlwaysOnDailyAnalytics | ShippingDailyAnalytics],
    ] = {
        camera: [] for camera in desired
    }
    for row in all_rows:
        rows_by_camera.setdefault(row.camera, []).append(row)

    cameras = []
    for camera in desired:
        camera_rows = rows_by_camera.get(camera, [])
        by_day = {row.day: row for row in camera_rows}
        colors = _merge_colors(camera_rows)
        color_items = _color_payload(colors)
        brands = _merge_brands(camera_rows)
        brand_items = _brand_payload(brands)
        cameras.append(
            _row_payload(by_day.get(day), camera, day, analytics_scope)
            | {
                "all_time_total": sum(row.total for row in camera_rows),
                "history": _history_payload(
                    by_day,
                    history_start,
                    day,
                    analytics_scope,
                ),
                "colors": color_items,
                "dominant_color": color_items[0]["color"] if color_items else None,
                "brands": brand_items,
                "dominant_brand": _dominant_brand(brand_items),
                "analytics_sync": _event_sync_payload(
                    cursors.get(camera),
                    now=now,
                    bootstrap_pending=camera in pending_bootstraps,
                ),
            }
        )

    aggregate_by_day: dict[date, dict] = {}
    for row in all_rows:
        if row.day < history_start:
            continue
        item = aggregate_by_day.setdefault(
            row.day,
            {
                "day": row.day.isoformat(),
                "model_total": 0,
                "model_per_color": {},
                "model_per_brand": {},
                "adjustment": 0,
                "total": 0,
                "updated_at": None,
            },
        )
        item["model_total"] += row.model_total
        item["adjustment"] += row.adjustment
        item["total"] += row.total
        item["updated_at"] = max(
            filter(None, [item["updated_at"], row.updated_at]), default=None
        )
        for color, value in (row.model_per_color or {}).items():
            item["model_per_color"][color] = item["model_per_color"].get(
                color, 0
            ) + int(value)
        for brand, value in _normalized_brands(
            row.model_per_brand,
            row.model_total,
        ).items():
            item["model_per_brand"][brand] = item["model_per_brand"].get(
                brand, 0
            ) + value
    history = []
    current = history_start
    while current <= day:
        item = aggregate_by_day.get(
            current,
            {
                "day": current.isoformat(),
                "model_total": 0,
                "model_per_color": {},
                "model_per_brand": {},
                "adjustment": 0,
                "total": 0,
                "updated_at": None,
            },
        )
        history.append(
            item
            | {
                "colors": _color_payload(item["model_per_color"]),
                "brands": _brand_payload(
                    _normalized_brands(item["model_per_brand"], item["model_total"])
                ),
            }
        )
        current += timedelta(days=1)
    all_colors = _merge_colors(all_rows)
    all_brands = _merge_brands(all_rows)
    brand_items = _brand_payload(all_brands)
    sync_rows = [item["analytics_sync"] for item in cameras]
    return {
        "analytics_scope": analytics_scope,
        "analytics_sync": _aggregate_sync_payload(sync_rows),
        "day": day.isoformat(),
        "total": sum(item["total"] for item in cameras),
        "all_time_total": sum(item["all_time_total"] for item in cameras),
        # Сумма цветов описывает распознанное моделью, итог — уже с ручными
        # поправками. Без этих двух чисел экран показывал бы «11670+2649+836,
        # а всего 15154» без объяснения, откуда разница.
        "model_all_time_total": sum(row.model_total for row in all_rows),
        "adjustment": sum(row.adjustment for row in all_rows),
        "history": history,
        "colors": _color_payload(all_colors),
        "dominant_color": _color_payload(all_colors)[0]["color"]
        if all_colors
        else None,
        "model_per_brand": all_brands,
        "brands": brand_items,
        "dominant_brand": _dominant_brand(brand_items),
        "cameras": cameras,
    }


@transaction.atomic
def archive_camera(camera: str, note: str, user) -> dict:
    """Закрыть период: накопленное уходит в архив, счётчик начинается с нуля.

    Обнуление не удаляет данные — дни остаются в истории и на графике, но
    выпадают из «сегодня» и «за всё время». Сырой cursor остаётся baseline
    camera-PC, поэтому после закрытия учитывается только новый прирост.
    """
    camera = _assert_ai247_reservation(camera)
    note = " ".join(str(note or "").split())[:500]

    # Serialize archive boundaries with event ingestion.  The API performs a
    # fresh journal drain immediately before entering here; this lock keeps a
    # concurrent page from being split across the old and new display period.
    cursor = (
        AlwaysOnCounterCursor.objects.select_for_update().filter(camera=camera).first()
    )
    if cursor is not None and cursor.event_sync_supported is True:
        if (
            cursor.event_caught_up_at is None
            or cursor.event_sync_error
            or cursor.event_sync_failed_at is not None
            or timezone.now() - cursor.event_caught_up_at
            > EVENT_ARCHIVE_MAX_SYNC_AGE
        ):
            raise ValidationError(
                {
                    "detail": "Сначала дождитесь синхронизации событий AI",
                    "code": "camera_events_not_synced",
                }
            )

    rows = list(
        AlwaysOnDailyAnalytics.objects.select_for_update()
        .filter(
            camera=camera,
            archived_at__isnull=True,
        )
        .order_by("day")
    )
    if not rows:
        raise ValidationError(
            {
                "detail": "Архивировать нечего — счётчик уже пуст",
                "code": "nothing_to_archive",
            }
        )

    now = timezone.now()
    archive = AlwaysOnCountArchive.objects.create(
        camera=camera,
        period_start=rows[0].day,
        period_end=rows[-1].day,
        model_total=sum(row.model_total for row in rows),
        model_per_color=_merge_colors(rows),
        model_per_brand=_merge_brands(rows),
        adjustment=sum(row.adjustment for row in rows),
        total=sum(row.total for row in rows),
        days=len(rows),
        note=note,
        archived_by=user,
    )
    # Сегодняшний день продолжает считаться после архивации, а уникальность
    # (camera, day) не даёт завести вторую живую строку. Поэтому текущий день
    # не помечаем архивным, а обнуляем: его накопленное уже лежит в архиве.
    today = timezone.localdate()
    closed = [row for row in rows if row.day != today]
    if closed:
        AlwaysOnDailyAnalytics.objects.filter(pk__in=[row.pk for row in closed]).update(
            archived_at=now, archive=archive
        )
    live_today = next((row for row in rows if row.day == today), None)
    if live_today is not None:
        # Живую строку обнуляем, но её вклад сохраняем снимком — иначе
        # разбивка архива по дням потеряла бы день закрытия.
        archive.day_rows = [
            {
                "day": today.isoformat(),
                "model_total": live_today.model_total,
                "adjustment": live_today.adjustment,
                "total": live_today.total,
                "model_per_color": _normalized_colors(live_today.model_per_color),
                "model_per_brand": _normalized_brands(
                    live_today.model_per_brand,
                    live_today.model_total,
                ),
            }
        ]
        archive.save(update_fields=["day_rows"])
        AlwaysOnDailyAnalytics.objects.filter(pk=live_today.pk).update(
            model_total=0,
            model_per_color={},
            model_per_brand={},
            adjustment=0,
        )
    # Сырой счётчик на camera-PC здесь не сбрасывается. Поэтому сохраняем его
    # baseline: если было 100, а после архива стало 140, в новый период должно
    # попасть только 40. Удаление cursor раньше повторно засчитывало все 140 и
    # могло бы задвоить автоматическую приёмку на склад.

    log_event(
        "always_on_count_archived",
        f"AI 24/7 · {camera}: счётчик обнулён, {archive.total} мешков "
        f"перенесены в архив" + (f". Примечание: {note}" if note else ""),
        user=user,
        payload={
            "camera": camera,
            "archive_id": archive.pk,
            "total": archive.total,
            "model_total": archive.model_total,
            "adjustment": archive.adjustment,
            "days": archive.days,
            "period_start": archive.period_start.isoformat(),
            "period_end": archive.period_end.isoformat(),
            "note": note,
        },
    )
    return _archive_payload(archive)


def _archive_day_rows(archive: AlwaysOnCountArchive) -> list[dict]:
    """Дни архива: помеченные строки плюс снимок дня закрытия."""
    rows = [
        {
            "day": row.day.isoformat(),
            "model_total": row.model_total,
            "adjustment": row.adjustment,
            "total": row.total,
            "colors": _color_payload(_normalized_colors(row.model_per_color)),
            "brands": _brand_payload(
                _normalized_brands(row.model_per_brand, row.model_total)
            ),
        }
        for row in archive.daily_rows.all()
    ]
    for snapshot in archive.day_rows or []:
        rows.append(
            {
                "day": snapshot.get("day"),
                "model_total": snapshot.get("model_total", 0),
                "adjustment": snapshot.get("adjustment", 0),
                "total": snapshot.get("total", 0),
                "colors": _color_payload(
                    _normalized_colors(snapshot.get("model_per_color"))
                ),
                "brands": _brand_payload(
                    _normalized_brands(
                        snapshot.get("model_per_brand"),
                        int(snapshot.get("model_total") or 0),
                    )
                ),
            }
        )
    return sorted(rows, key=lambda item: item["day"] or "", reverse=True)


def _archive_payload(archive: AlwaysOnCountArchive) -> dict:
    return {
        "id": archive.pk,
        "camera": archive.camera,
        "period_start": archive.period_start.isoformat(),
        "period_end": archive.period_end.isoformat(),
        "model_total": archive.model_total,
        "adjustment": archive.adjustment,
        "total": archive.total,
        "days": archive.days,
        "colors": _color_payload(_normalized_colors(archive.model_per_color)),
        "brands": _brand_payload(
            _normalized_brands(archive.model_per_brand, archive.model_total)
        ),
        "day_rows": _archive_day_rows(archive),
        "note": archive.note,
        "archived_by_name": (
            archive.archived_by.username if archive.archived_by else None
        ),
        "created_at": archive.created_at,
    }


@transaction.atomic
def delete_archive(archive_id: int, user) -> dict:
    """Удалить запись архива, вернув её дни в текущий счёт.

    Архивация — это перенос, а не списание, поэтому и отмена возвращает:
    помеченные дни снова становятся живыми и попадают в «за всё время».
    Иначе ошибочное закрытие уничтожило бы уже посчитанные мешки.

    День закрытия возвращать некуда — он продолжил считаться дальше, и его
    строка уже занята новыми мешками. Его вклад лежал снимком в day_rows,
    поэтому он прибавляется к этой строке, а не подменяет её.
    """
    archive = (
        AlwaysOnCountArchive.objects.select_for_update().filter(pk=archive_id).first()
    )
    if archive is None:
        raise ValidationError(
            {
                "detail": "Запись архива не найдена",
                "code": "archive_not_found",
            }
        )
    _assert_ai247_reservation(archive.camera)

    restored_days = archive.daily_rows.count()
    AlwaysOnDailyAnalytics.objects.filter(archive=archive).update(
        archived_at=None, archive=None
    )

    # Снимок дня закрытия: возвращаем его мешки в ту же дату.
    for snapshot in archive.day_rows or []:
        day = snapshot.get("day")
        if not day:
            continue
        row, _ = AlwaysOnDailyAnalytics.objects.select_for_update().get_or_create(
            camera=archive.camera,
            day=day,
        )
        snapshot_model_total = int(snapshot.get("model_total") or 0)
        current_brands = _normalized_brands(row.model_per_brand, row.model_total)
        row.model_total += snapshot_model_total
        row.adjustment += int(snapshot.get("adjustment") or 0)
        merged = dict(row.model_per_color or {})
        for color, value in _normalized_colors(snapshot.get("model_per_color")).items():
            merged[color] = int(merged.get(color, 0)) + value
        row.model_per_color = merged
        merged_brands = dict(current_brands)
        for brand, value in _normalized_brands(
            snapshot.get("model_per_brand"),
            snapshot_model_total,
        ).items():
            merged_brands[brand] = int(merged_brands.get(brand, 0)) + value
        row.model_per_brand = merged_brands
        row.save(
            update_fields=[
                "model_total",
                "adjustment",
                "model_per_color",
                "model_per_brand",
                "updated_at",
            ]
        )
        restored_days += 1

    payload = _archive_payload(archive)
    archive.delete()
    log_event(
        "always_on_archive_deleted",
        f"AI 24/7 · {archive.camera}: запись архива удалена, "
        f"{payload['total']} мешков возвращены в счёт",
        user=user,
        payload={
            "camera": archive.camera,
            "archive_id": archive_id,
            "total": payload["total"],
            "days": restored_days,
            "period_start": payload["period_start"],
            "period_end": payload["period_end"],
        },
    )
    return payload


def archives_payload(
    camera: str | None = None,
    *,
    camera_sources: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    rows = AlwaysOnCountArchive.objects.select_related("archived_by").prefetch_related(
        "daily_rows"
    )
    if camera_sources is None:
        camera_sources = MonoblockCameraSettings.reserved_sources(
            ANALYTICS_SCOPE_AI247
        )
    if camera:
        rows = rows.filter(camera=ai.normalize(camera))
    rows = rows.filter(camera__in=camera_sources)
    return [_archive_payload(row) for row in rows]


@transaction.atomic
def subtract_today(camera: str, amount, reason: str, user, color: str) -> dict:
    camera = _assert_ai247_reservation(camera)
    # Production corrections acquire cursor→batch.  Take the cursor before
    # this function locks the daily row to preserve that global lock order.
    AlwaysOnCounterCursor.objects.select_for_update().get_or_create(camera=camera)
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        raise ValidationError({"amount": "Укажите количество больше нуля"})
    reason = " ".join(str(reason or "").split())
    if len(reason) < 5:
        raise ValidationError({"reason": "Укажите причину (минимум 5 символов)"})
    if len(reason) > 500:
        raise ValidationError({"reason": "Причина слишком длинная"})
    color = str(color or "").strip().lower()
    if not color:
        raise ValidationError({"color": "Выберите цвет продукции"})

    day = timezone.localdate()
    row, _ = AlwaysOnDailyAnalytics.objects.select_for_update().get_or_create(
        camera=camera,
        day=day,
    )
    before = row.total
    if amount > before:
        raise ValidationError(
            {
                "amount": f"Нельзя вычесть больше текущего итога ({before})",
            }
        )
    available_color = int((row.model_per_color or {}).get(color, 0))
    if amount > available_color:
        raise ValidationError(
            {
                "amount": f"Для цвета доступно только {available_color}",
            }
        )

    from . import production

    production.record_correction(
        camera=camera,
        color=color,
        amount=amount,
        reason=reason,
        user=user,
    )
    row.adjustment -= amount
    row.save(update_fields=["adjustment", "updated_at"])
    log_event(
        "always_on_count_adjustment",
        f"AI 24/7 · {camera}: итог уменьшен на {amount}. Причина: {reason}",
        user=user,
        payload={
            "camera": camera,
            "day": day.isoformat(),
            "color": color,
            "amount": amount,
            "before": before,
            "after": row.total,
            "reason": reason,
            "model_total": row.model_total,
            "adjustment": row.adjustment,
        },
    )
    return _row_payload(row, camera, day)
