from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.grain import services as grain_services

from . import ai, analytics
from .models import MonoblockCameraSettings

log = logging.getLogger(__name__)


def reconcile() -> dict:
    """Make the camera-PC durable state match PostgreSQL's desired state."""
    desired = sorted(MonoblockCameraSettings.always_on_sources())
    current = ai.always_on_status()
    current_sources = current.get("cameras")
    current_source = current.get("source", "sub")
    if not isinstance(current_sources, list):
        # Ответ без разборного списка камер — это «состояние неизвестно», а не
        # «камер нет». Раньше он приводился к [] и, если в PostgreSQL тоже было
        # пусто, расхождения не возникало — зато при непустом выборе монитор
        # честно перезаписывал ПК. Обратный случай опаснее: считать выбор
        # применённым нельзя, но и продавливать что-либо по неизвестному
        # состоянию мы не будем — просто ждём следующей итерации.
        log.warning(
            "Камера-ПК вернул always-on без списка камер (%r) — "
            "состояние неизвестно, синхронизация отложена",
            current_sources,
        )
        analytics.record_snapshot(current)
        return current
    if sorted(current_sources) != desired or current_source != "sub":
        current = ai.configure_always_on(desired, "sub")
    analytics.record_snapshot(current)
    return current


def reconcile_wagon_number() -> dict:
    """Keep the durable wagon-number camera role in sync after restarts."""
    desired = MonoblockCameraSettings.wagon_number_source() or None
    current = ai.wagon_number_status()
    if current.get("camera") != desired or current.get("source") != "main":
        current = ai.configure_wagon_number(desired, "main")
    return current


# Как часто спрашиваем камеру про табличку вагона. Состав стоит под разгрузкой
# долго, поэтому минуты достаточно: чаще — лишняя нагрузка на модель, реже —
# заметная задержка появления рейса на экране.
WAGON_PLATE_PERIOD = timedelta(minutes=1)
WAGON_PLATE_STATE_KEY = "cameras:wagon-plate-last-poll:v1"


def poll_wagon_plate() -> dict:
    """Открыть приход, когда камера видит табличку вагона.

    Заменяет отсутствующий датчик прибытия. Модель находит табличку, но цифры
    не читает, поэтому рейс заводится без номера — важны факт и время заезда.

    Опрос идёт реже цикла мониторинга: тот крутится каждые 30 секунд, а
    спрашивать модель чаще раза в минуту незачем.
    """
    camera = MonoblockCameraSettings.wagon_number_source()
    if not camera:
        return {"skipped": "no_camera"}

    last = cache.get(WAGON_PLATE_STATE_KEY)
    now = timezone.now()
    if last and now - last < WAGON_PLATE_PERIOD:
        return {"skipped": "too_soon"}
    cache.set(WAGON_PLATE_STATE_KEY, now, int(WAGON_PLATE_PERIOD.total_seconds()) * 4)

    seen = ai.wagon_plate_seen(camera)
    if seen is None:
        # Нет кадра или сервис молчит. Это «неизвестно», а не «поезда нет»:
        # молча закрывать по такому ответу ничего нельзя.
        return {"seen": None}
    if not seen:
        return {"seen": False}

    wagon = grain_services.register_detected_arrival(camera_source=camera)
    if wagon is None:
        # Тот же состав всё ещё под камерой — рейс уже заведён.
        return {"seen": True, "created": None}
    log.info("Камера %s зафиксировала прибытие состава: рейс #%s", camera, wagon.pk)
    return {"seen": True, "created": wagon.pk}
