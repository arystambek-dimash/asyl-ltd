from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from . import ai
from .models import (
    AlwaysOnCountArchive,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    MonoblockCameraSettings,
)

EVENT_ARCHIVE_MAX_SYNC_AGE = timedelta(seconds=60)


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


def record_model_delta(
    *,
    camera: str,
    color_delta: dict[str, int],
    total_delta: int,
    observed_at: datetime,
    ordered_color_event: bool = False,
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

    # The daily aggregate remains the fast source for charts.  The append-only
    # production ledger is deliberately separate: display archives may be
    # created/deleted by an administrator, while warehouse receipts must never
    # be duplicated or lost because of those presentation actions.
    from . import production

    production.record_color_deltas(
        camera=camera,
        color_deltas=color_delta,
        total_delta=total_delta,
        observed_at=observed_at,
        ordered_color_event=ordered_color_event,
    )

    row, _ = AlwaysOnDailyAnalytics.objects.select_for_update().get_or_create(
        camera=camera,
        day=timezone.localdate(observed_at),
    )
    merged_colors = dict(row.model_per_color or {})
    for color, value in color_delta.items():
        merged_colors[color] = int(merged_colors.get(color, 0)) + value
    row.model_total += total_delta
    row.model_per_color = merged_colors
    row.save(update_fields=["model_total", "model_per_color", "updated_at"])


@transaction.atomic
def _record_processor(processor: dict, observed_at: datetime) -> None:
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
    )


def record_snapshot(
    live: dict,
    observed_at: datetime | None = None,
    *,
    cameras: set[str] | None = None,
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
        _record_processor(processor, observed_at)


def _row_payload(row: AlwaysOnDailyAnalytics | None, camera: str, day: date) -> dict:
    colors = _normalized_colors(row.model_per_color if row else None)
    return {
        "camera": camera,
        "day": day.isoformat(),
        "model_total": row.model_total if row else 0,
        "model_per_color": colors,
        # Готовая разбивка за день с процентами — её показывает клик по
        # столбику, и считается она там же, где общая, чтобы цифры сходились.
        "colors": _color_payload(colors),
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


def _color_payload(colors: dict[str, int]) -> list[dict]:
    """Доли цветов, дающие в сумме ровно 100%.

    Округление каждой доли по отдельности давало 72.3 + 21.3 + 6.5 = 100.1%.
    Считаем в десятых долях процента и раздаём остаток по наибольшему
    дробному хвосту (метод наибольших остатков), поэтому сумма сходится.
    """
    total = sum(colors.values())
    rows = sorted(colors.items(), key=lambda item: (-item[1], item[0]))
    if not total:
        return [{"color": color, "total": value, "percent": 0} for color, value in rows]

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
        {"color": color, "total": value, "percent": tenths[index] / 10}
        for index, (color, value) in enumerate(rows)
    ]


def _history_payload(
    rows_by_day: dict[date, AlwaysOnDailyAnalytics], start: date, end: date
) -> list[dict]:
    result = []
    current = start
    while current <= end:
        result.append(
            _row_payload(rows_by_day.get(current), "", current) | {"camera": None}
        )
        current += timedelta(days=1)
    return result


def today_payload() -> dict:
    day = timezone.localdate()
    history_start = day - timedelta(days=13)
    desired = MonoblockCameraSettings.always_on_sources()
    # Архивные дни остаются в базе ради истории, но в текущий счёт не входят:
    # их мешки уже посчитаны и перенесены в архив.
    all_rows = list(
        AlwaysOnDailyAnalytics.objects.filter(
            camera__in=desired, archived_at__isnull=True
        )
    )
    rows_by_camera: dict[str, list[AlwaysOnDailyAnalytics]] = {
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
        cameras.append(
            _row_payload(by_day.get(day), camera, day)
            | {
                "all_time_total": sum(row.total for row in camera_rows),
                "history": _history_payload(by_day, history_start, day),
                "colors": color_items,
                "dominant_color": color_items[0]["color"] if color_items else None,
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
    history = []
    current = history_start
    while current <= day:
        item = aggregate_by_day.get(
            current,
            {
                "day": current.isoformat(),
                "model_total": 0,
                "model_per_color": {},
                "adjustment": 0,
                "total": 0,
                "updated_at": None,
            },
        )
        history.append(item | {"colors": _color_payload(item["model_per_color"])})
        current += timedelta(days=1)
    all_colors = _merge_colors(all_rows)
    return {
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
        "cameras": cameras,
    }


@transaction.atomic
def archive_camera(camera: str, note: str, user) -> dict:
    """Закрыть период: накопленное уходит в архив, счётчик начинается с нуля.

    Обнуление не удаляет данные — дни остаются в истории и на графике, но
    выпадают из «сегодня» и «за всё время». Сырой cursor остаётся baseline
    camera-PC, поэтому после закрытия учитывается только новый прирост.
    """
    camera = ai.normalize(camera)
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
        .filter(camera=camera, archived_at__isnull=True)
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
            }
        ]
        archive.save(update_fields=["day_rows"])
        AlwaysOnDailyAnalytics.objects.filter(pk=live_today.pk).update(
            model_total=0, model_per_color={}, adjustment=0
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
        row.model_total += int(snapshot.get("model_total") or 0)
        row.adjustment += int(snapshot.get("adjustment") or 0)
        merged = dict(row.model_per_color or {})
        for color, value in _normalized_colors(snapshot.get("model_per_color")).items():
            merged[color] = int(merged.get(color, 0)) + value
        row.model_per_color = merged
        row.save(
            update_fields=["model_total", "adjustment", "model_per_color", "updated_at"]
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


def archives_payload(camera: str | None = None) -> list[dict]:
    rows = AlwaysOnCountArchive.objects.select_related("archived_by").prefetch_related(
        "daily_rows"
    )
    if camera:
        rows = rows.filter(camera=ai.normalize(camera))
    return [_archive_payload(row) for row in rows]


@transaction.atomic
def subtract_today(camera: str, amount, reason: str, user, color: str) -> dict:
    camera = ai.normalize(camera)
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
