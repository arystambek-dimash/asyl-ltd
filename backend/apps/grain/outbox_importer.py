"""Replay the collector's immutable evidence, then acknowledge after DB commit."""

import os
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from weighbridge.outbox import Outbox
from . import passage_scale_automation as automation, weighing_photos
from .models import AutomaticPassageCapture as Capture, PassageScaleAutomationState as Lane, WeighingPhotoDelivery


def directory():
    return Path(os.environ.get("WEIGHBRIDGE_OUTBOX_DIR", "/var/lib/weighbridge"))


def enabled():
    return (directory() / "enabled").is_file()


def _bound_camera_frame(event):
    if "recognition_frame_bound" in event:
        return event["recognition_frame_bound"] is True
    # Compatibility with already persisted collector events: an error payload's
    # direction was retained ONLY when the response arrived during this truck's
    # occupancy. No direction/late response is not permission to retry a camera.
    return bool(event.get("recognition")) or (
        event.get("orientation") in {"front", "rear"}
        and event.get("recognition_error") == "recognition_unavailable"
    )


def _store_evidence(photo, event):
    if event.get("photo") and not photo.photo:
        name = f"grain/evidence/{photo.request_id}.jpg"
        if not photo.photo.storage.exists(name):
            name = photo.photo.storage.save(name, ContentFile(event["photo"]))
        photo.photo.name = name
    photo.snapshot_attempted = True  # collector captures are never live-retried
    if photo.photo:
        photo.status, photo.error_code = "saved", ""
    elif _bound_camera_frame(event):
        photo.status, photo.error_code = "pending", "collector_frame_pending"
        photo.next_attempt_at = timezone.now()
    else:
        photo.status, photo.error_code = "unavailable", "collector_photo_unavailable"
    photo.save()
    weighing_photos._link_photo(photo)


def recover_collector_photos(box, *, limit=20):
    """Recover acknowledged older releases' missing photos from the same UUID.

    This does not reopen accounting events or issue a fresh camera capture.
    Missing files already in the durable outbox can also be restored directly.
    """
    repaired = 0
    jobs = WeighingPhotoDelivery.objects.filter(
        status="unavailable", error_code="collector_photo_unavailable",
    ).select_related("capture").order_by("-id")[:limit]
    for job in jobs:
        event = box.evidence(job.request_id)
        if event and (event.get("photo") or _bound_camera_frame(event)):
            with transaction.atomic():
                locked = WeighingPhotoDelivery.objects.select_for_update().get(pk=job.pk)
                if locked.status != "unavailable" or locked.error_code != "collector_photo_unavailable":
                    continue
                _store_evidence(locked, event)
            repaired += 1
    return repaired


@transaction.atomic
def import_event(event):
    if event.get("version") != 1:
        raise ValueError("Unsupported outbox version; event retained")
    key = UUID(event["id"])
    weight = event["weight_kg"]
    occurred = parse_datetime(event["stable_weight_at"])
    if (type(weight) is not int or not 500 < weight <= settings.TRUCK_SCALE_MAX_WEIGHT_KG
            or occurred is None or timezone.is_naive(occurred) or occurred > timezone.now()):
        raise ValueError("Invalid outbox weight or timestamp; event retained")
    Lane.objects.get_or_create(scale_number="truck")
    Lane.objects.select_for_update().get(scale_number="truck")
    capture, created = Capture.objects.get_or_create(idempotency_key=key, defaults={
        "camera": event["camera"], "weight_kg": weight, "trigger_weight_kg": weight,
        "stable_weight_at": occurred, "scale_age_seconds": Decimal(event["scale_age_seconds"]),
        "scale_updated_at": event.get("scale_updated_at") or "", "attempt_request_id": key,
        "attempt_stable_weight_at": occurred, "stage": Capture.RECOGNIZING,
        "recognition_attempts": 1, "recognition_dispatched": True,
        "requires_acknowledgement": False, "orientation": event.get("orientation") or "",
    })
    if capture.weight_kg != weight or capture.stable_weight_at != occurred or capture.camera != event["camera"]:
        raise ValueError("Outbox UUID conflict; event retained")
    if not created and capture.status in {Capture.COMPLETED, Capture.FAILED}:
        return capture
    photo, _ = WeighingPhotoDelivery.objects.get_or_create(request_id=key, defaults={"camera": capture.camera, "capture": capture})
    _store_evidence(photo, event)
    if event.get("recognition"):
        try:
            automation._persist_recognition(capture.pk, event["recognition"])
        except (automation._CaptureRejected, KeyError, ValueError, TypeError):
            capture.refresh_from_db()
            automation._mark_plate_unresolved(capture, now=timezone.now(), code="collector_recognition_invalid", detail="Вес сохранён сборщиком; номер требует проверки.")
    else:
        automation._mark_plate_unresolved(capture, now=timezone.now(), code="collector_plate_unresolved", detail="Вес сохранён сборщиком; номер требует проверки.")
    capture = automation._apply_recognized_capture(capture.pk)
    if capture.status not in {Capture.COMPLETED, Capture.FAILED}:
        raise ValueError("Outbox apply incomplete; retry retained")
    weighing_photos._link_photo(photo)
    return capture


def poll_once():
    box = Outbox(directory())
    box.state("config", automation.scale_automation_settings())
    for _ in range(10):
        event = box.next()
        if event is None:
            break
        import_event(event)  # atomic decorator COMMITs before ack
        box.ack(event["id"])
    last_repair = box.state("photo_recovery") or {}
    if time.time() - last_repair.get("checked_at", 0) >= 30:
        recover_collector_photos(box)
        box.state("photo_recovery", {"checked_at": time.time()})
    heartbeat = box.state("heartbeat") or {}
    stale = time.time() - heartbeat.get("updated_at", 0) > 10
    state = "unavailable" if stale or heartbeat.get("status") != "running" else (
        "idle" if heartbeat.get("clear") else "awaiting_clear" if heartbeat.get("current") else "candidate"
    )
    automation._store_runtime({
        "enabled": True, "state": state, "heartbeat_stale": stale,
        "last_checked_at": timezone.now().isoformat(), "active": None,
        "stable_weight_seconds": automation.scale_automation_settings()["stable_weight_seconds"],
        "collector": {**box.counts(), "status": heartbeat.get("status", "starting")},
    })
    return automation.MonitorIteration(state=state)
