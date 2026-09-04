"""Best-effort download of the Camera-PC evidence frame for a weighing.

The weight is already committed by the time this runs. A missing or failed
photo is logged and forgotten: it must never roll back or delay accounting,
and it must never run inside a database transaction (network I/O).
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.core.files.base import ContentFile

from apps.cameras import ai as camera_ai

from .models import UnassignedWeighing, WeighingRecord

log = logging.getLogger(__name__)


def _photo_target(request_id: UUID):
    weighing = (
        WeighingRecord.objects.filter(photo_request_id=request_id)
        .order_by("-id")
        .first()
    )
    if weighing is not None:
        return weighing
    return (
        UnassignedWeighing.objects.filter(photo_request_id=request_id)
        .order_by("-id")
        .first()
    )


def attach_photo(camera: str, request_id: UUID | str | None) -> bool:
    """Fetch and store the frame for ``request_id``; ``True`` when saved."""

    if not request_id or not camera:
        return False
    try:
        parsed = UUID(str(request_id))
    except (AttributeError, TypeError, ValueError):
        return False
    target = _photo_target(parsed)
    if target is None or target.photo:
        return False
    try:
        frame = camera_ai.fetch_vehicle_recognition_frame(camera, str(parsed))
    except (camera_ai.AiUnavailable, camera_ai.AiError, ValueError) as exc:
        log.warning(
            "Weighing photo unavailable request_id=%s camera=%s: %s",
            parsed,
            camera,
            exc,
        )
        return False
    if not frame:
        return False
    try:
        target.photo.save(f"{parsed}.jpg", ContentFile(frame), save=True)
    except OSError:
        log.exception("Could not store weighing photo request_id=%s", parsed)
        return False
    return True


__all__ = ["attach_photo"]
