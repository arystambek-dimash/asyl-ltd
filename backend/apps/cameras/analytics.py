from __future__ import annotations

from datetime import date, datetime

from django.db import transaction
from django.db.models import F
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

    cursor, created = AlwaysOnCounterCursor.objects.select_for_update().get_or_create(
        camera=camera,
        defaults={"last_total": total, "last_mode": str(processor.get("mode") or "")},
    )
    if created:
        delta = total
    elif total >= cursor.last_total:
        delta = total - cursor.last_total
    else:
        # Сервис/процессор перезапущен и сырой счётчик начал новый цикл.
        delta = total

    cursor.last_total = total
    cursor.last_mode = str(processor.get("mode") or "")[:16]
    cursor.save(update_fields=["last_total", "last_mode", "updated_at"])

    # Сессионная погрузка учитывается в заказе, но не в фоновой аналитике.
    if processor.get("mode") != "always_on" or not processor.get("running") or delta <= 0:
        return

    row, _ = AlwaysOnDailyAnalytics.objects.get_or_create(camera=camera, day=day)
    AlwaysOnDailyAnalytics.objects.filter(pk=row.pk).update(
        model_total=F("model_total") + delta,
    )


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
        "adjustment": row.adjustment if row else 0,
        "total": row.total if row else 0,
        "updated_at": row.updated_at if row else None,
    }


def today_payload() -> dict:
    day = timezone.localdate()
    desired = MonoblockCameraSettings.always_on_sources()
    rows = {
        row.camera: row
        for row in AlwaysOnDailyAnalytics.objects.filter(day=day, camera__in=desired)
    }
    cameras = [_row_payload(rows.get(camera), camera, day) for camera in desired]
    return {
        "day": day.isoformat(),
        "total": sum(item["total"] for item in cameras),
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
