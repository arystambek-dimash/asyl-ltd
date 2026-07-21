from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from . import ai
from .models import (
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    MonoblockCameraSettings,
)


def _observed_day(observed_at: datetime | None = None) -> date:
    return timezone.localdate(observed_at or timezone.now())


def _processor_total(processor: dict) -> int | None:
    value = processor.get("total")
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
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


def _counter_delta(current: int, previous: int) -> int:
    # После перезапуска процесса сырой счётчик снова начинается с нуля.
    return current - previous if current >= previous else current


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


@transaction.atomic
def _record_processor(processor: dict, day: date) -> None:
    camera = processor.get("cam")
    total = _processor_total(processor)
    if not isinstance(camera, str) or total is None:
        return
    try:
        camera = ai.normalize(camera)
    except ai.AiError:
        return
    colors = _processor_colors(processor)

    cursor, created = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=camera,
        defaults={
            "last_total": total,
            "last_per_color": colors,
            "last_mode": str(processor.get("mode") or ""),
        },
    )
    if created:
        delta = total
        color_delta = colors
    else:
        delta = _counter_delta(total, cursor.last_total)
        color_delta = _color_delta(colors, cursor.last_per_color)

    cursor.last_total = total
    cursor.last_per_color = colors
    cursor.last_mode = str(processor.get("mode") or "")[:16]
    cursor.save(update_fields=["last_total", "last_per_color", "last_mode", "updated_at"])

    # Сессионная погрузка учитывается в заказе, но не в фоновой аналитике.
    if processor.get("mode") != "always_on" or not processor.get("running") or delta <= 0:
        return

    row, _ = AlwaysOnDailyAnalytics.objects.select_for_update().get_or_create(
        camera=camera, day=day,
    )
    merged_colors = dict(row.model_per_color or {})
    for color, value in color_delta.items():
        merged_colors[color] = int(merged_colors.get(color, 0)) + value
    row.model_total += delta
    row.model_per_color = merged_colors
    row.save(update_fields=["model_total", "model_per_color", "updated_at"])


def record_snapshot(live: dict, observed_at: datetime | None = None) -> None:
    day = _observed_day(observed_at)
    processors = live.get("processors") if isinstance(live, dict) else None
    if not isinstance(processors, list):
        return
    for processor in processors:
        if isinstance(processor, dict):
            _record_processor(processor, day)


def _row_payload(row: AlwaysOnDailyAnalytics | None, camera: str, day: date) -> dict:
    return {
        "camera": camera,
        "day": day.isoformat(),
        "model_total": row.model_total if row else 0,
        "model_per_color": dict(row.model_per_color or {}) if row else {},
        "adjustment": row.adjustment if row else 0,
        "total": row.total if row else 0,
        "updated_at": row.updated_at if row else None,
    }


def _merge_colors(rows) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        for color, value in (row.model_per_color or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            result[color] = result.get(color, 0) + max(0, int(value))
    return result


def _color_payload(colors: dict[str, int]) -> list[dict]:
    total = sum(colors.values())
    return [
        {
            "color": color,
            "total": value,
            "percent": round(value * 100 / total, 1) if total else 0,
        }
        for color, value in sorted(colors.items(), key=lambda item: (-item[1], item[0]))
    ]


def _history_payload(rows_by_day: dict[date, AlwaysOnDailyAnalytics], start: date, end: date) -> list[dict]:
    result = []
    current = start
    while current <= end:
        result.append(_row_payload(rows_by_day.get(current), "", current) | {"camera": None})
        current += timedelta(days=1)
    return result


def today_payload() -> dict:
    day = timezone.localdate()
    history_start = day - timedelta(days=13)
    desired = MonoblockCameraSettings.always_on_sources()
    all_rows = list(AlwaysOnDailyAnalytics.objects.filter(camera__in=desired))
    rows_by_camera: dict[str, list[AlwaysOnDailyAnalytics]] = {camera: [] for camera in desired}
    for row in all_rows:
        rows_by_camera.setdefault(row.camera, []).append(row)

    cameras = []
    for camera in desired:
        camera_rows = rows_by_camera.get(camera, [])
        by_day = {row.day: row for row in camera_rows}
        colors = _merge_colors(camera_rows)
        color_items = _color_payload(colors)
        cameras.append(
            _row_payload(by_day.get(day), camera, day) | {
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
        item = aggregate_by_day.setdefault(row.day, {
            "day": row.day.isoformat(), "model_total": 0, "model_per_color": {},
            "adjustment": 0, "total": 0, "updated_at": None,
        })
        item["model_total"] += row.model_total
        item["adjustment"] += row.adjustment
        item["total"] += row.total
        item["updated_at"] = max(filter(None, [item["updated_at"], row.updated_at]), default=None)
        for color, value in (row.model_per_color or {}).items():
            item["model_per_color"][color] = item["model_per_color"].get(color, 0) + int(value)
    history = []
    current = history_start
    while current <= day:
        history.append(aggregate_by_day.get(current, {
            "day": current.isoformat(), "model_total": 0, "model_per_color": {},
            "adjustment": 0, "total": 0, "updated_at": None,
        }))
        current += timedelta(days=1)
    all_colors = _merge_colors(all_rows)
    return {
        "day": day.isoformat(),
        "total": sum(item["total"] for item in cameras),
        "all_time_total": sum(item["all_time_total"] for item in cameras),
        "history": history,
        "colors": _color_payload(all_colors),
        "dominant_color": _color_payload(all_colors)[0]["color"] if all_colors else None,
        "cameras": cameras,
    }


@transaction.atomic
def subtract_today(camera: str, amount, reason: str, user) -> dict:
    camera = ai.normalize(camera)
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

    day = timezone.localdate()
    row, _ = AlwaysOnDailyAnalytics.objects.select_for_update().get_or_create(
        camera=camera, day=day,
    )
    before = row.total
    if amount > before:
        raise ValidationError({
            "amount": f"Нельзя вычесть больше текущего итога ({before})",
        })
    row.adjustment -= amount
    row.save(update_fields=["adjustment", "updated_at"])
    log_event(
        "always_on_count_adjustment",
        f"AI 24/7 · {camera}: итог уменьшен на {amount}. Причина: {reason}",
        user=user,
        payload={
            "camera": camera, "day": day.isoformat(), "amount": amount,
            "before": before, "after": row.total, "reason": reason,
            "model_total": row.model_total, "adjustment": row.adjustment,
        },
    )
    return _row_payload(row, camera, day)
