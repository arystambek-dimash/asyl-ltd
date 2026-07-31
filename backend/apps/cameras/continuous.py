from __future__ import annotations

import logging

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
