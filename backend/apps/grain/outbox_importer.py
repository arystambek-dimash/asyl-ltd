"""Replay the collector's immutable evidence, then acknowledge after DB commit."""

import logging
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.cameras import ai as camera_ai
from weighbridge.outbox import Outbox, is_busy
from . import passage_scale_automation as automation, services, weighing_photos
from .models import AutomaticPassageCapture as Capture, PassageScaleAutomationState as Lane, WeighingPhotoDelivery
from .vehicle_weight_capture import _safe_ai_payload

log = logging.getLogger(__name__)

# The collector heartbeats every second; older than this it is not proof of life.
STALE_AFTER_SECONDS = 10
# A single missed heartbeat or one failed scale read is not an outage worth
# flipping the operator's status: the collector must look down this long.
UNAVAILABLE_GRACE_SECONDS = 20
PHOTO_RECOVERY_INTERVAL_SECONDS = 30
BUSY_LOG_INTERVAL_SECONDS = 60

# What this process already knows about each outbox, so a poll neither rewrites
# unchanged state into the collector's SQLite file (every write competes with
# the collector for the lock) nor forgets the last confirmed state while the
# file is briefly locked.
_memory: dict[str, dict] = {}


def directory():
    return Path(os.environ.get("WEIGHBRIDGE_OUTBOX_DIR", "/var/lib/weighbridge"))


def enabled():
    return (directory() / "enabled").is_file()


def _remembered(path):
    return _memory.setdefault(str(path), {
        "config": None, "photo_recovery_at": 0.0, "state": None, "down_since": None, "busy_logged_at": 0.0,
    })


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


def _votes_word(count):
    if count % 10 == 1 and count % 100 != 11:
        return "голос"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "голоса"
    return "голосов"


def _weak_plate(votes):
    """The one plate the camera voted for short of confirmation, as ``(number, votes)``.

    Two different readings in one window mean the frames disagree, one reading
    with a single vote is a lone OCR guess: neither may name the truck. A tally
    past the Camera-PC's own ceiling is no count ``confirmation_votes`` can hold.
    """
    voted = [(number, count) for number, count in votes.items() if count > 0]
    if (len(voted) != 1 or not 2 <= voted[0][1] <= camera_ai.MAX_VEHICLE_CONFIRMATION_VOTES
            or not services.KZ_VEHICLE_PLATE_RE.fullmatch(voted[0][0])):
        return "", None
    return voted[0]


def _tally(votes):
    ranked = sorted(votes.items(), key=lambda pair: (-pair[1], pair[0]))
    return ", ".join(f"{count} {_votes_word(count)} за {number}" for number, count in ranked)


def _failure_detail(diagnostics, error):
    votes = diagnostics.get("votes") or {}
    detected, scanned = diagnostics.get("detected_frames"), diagnostics.get("frames_scanned")
    if error == "no_match":
        # Only a refusal that searched the whole window can say how the search went.
        if votes:
            needed = diagnostics.get("confirmation_votes")
            return f"Номер не подтверждён: {_tally(votes)}" + (f" (нужно {needed})" if needed else "")
        if detected == 0:
            detail = "Камера не нашла табличку" + (f": 0 из {scanned} кадров" if scanned else "")
            # The Camera-PC looked again at zoomed tiles of those frames (a
            # zoomed hit counts as a detected frame, so here it found nothing).
            if diagnostics.get("zoom_frames"):
                detail += ", зум по тайлам тоже пуст"
            return detail
        if detected:
            return f"Номер не прочитан: табличка в {detected} из {scanned or '?'} кадров"
    status = f"Камера: {diagnostics.get('status') or error}"
    # A search cut short (timeout, lost trigger) names its status first; its
    # partial tally is context for the operator, never a plate to trust.
    return f"{status}; голоса: {_tally(votes)}" if votes else status


def _store_diagnostics(capture, diagnostics, error, *, now):
    """Keep the Camera-PC refusal on the capture so the CRM can read it, not just "no plate".

    A single plate short of confirmation (two votes of three) is retained as a
    weak number: the identity worker books it only for a truck already on site.
    """
    safe = _safe_ai_payload(diagnostics)
    raw_votes = diagnostics.get("votes")
    # A tally cut down by the collector or on import may have lost a competing
    # number, and a search cut short never saw the whole window: only a whole
    # tally of a finished no_match search can single out one plate.
    trimmed = (
        safe.get("votes_truncated") is True
        or (isinstance(raw_votes, dict) and len(raw_votes) > len(safe.get("votes") or {}))
    )
    number, votes = _weak_plate(safe.get("votes") or {}) if error == "no_match" and not trimmed else ("", None)
    if number:
        safe["weak_plate"] = True
    automation._mark_plate_unresolved(capture, now=now, code="collector_plate_unresolved", detail=_failure_detail(safe, error))
    capture.ai_payload_json = safe
    capture.vehicle_number = number
    capture.confirmation_votes = votes
    capture.response_status = 422 if error == "no_match" else None
    capture.save(update_fields=["ai_payload_json", "vehicle_number", "confirmation_votes", "response_status", "updated_at"])


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
    elif isinstance(event.get("recognition_diagnostics"), dict) and event["recognition_diagnostics"]:
        _store_diagnostics(capture, event["recognition_diagnostics"], event.get("recognition_error") or "", now=timezone.now())
    else:
        automation._mark_plate_unresolved(capture, now=timezone.now(), code="collector_plate_unresolved", detail="Вес сохранён сборщиком; номер требует проверки.")
    capture = automation._apply_recognized_capture(capture.pk)
    if capture.status not in {Capture.COMPLETED, Capture.FAILED}:
        raise ValueError("Outbox apply incomplete; retry retained")
    weighing_photos._link_photo(photo)
    return capture


def _collector_state(heartbeat, remembered):
    """Project the heartbeat onto the lane state, smoothing short blips.

    ``unavailable`` is reported once the collector has looked down for
    UNAVAILABLE_GRACE_SECONDS (or when nothing better was ever confirmed);
    until then the last confirmed state stands.
    """
    stale = time.time() - heartbeat.get("updated_at", 0) > STALE_AFTER_SECONDS
    down = stale or heartbeat.get("status") != "running"
    now = time.monotonic()
    if not down:
        remembered["down_since"] = None
        state = "idle" if heartbeat.get("clear") else "awaiting_clear" if heartbeat.get("current") else "candidate"
    else:
        if remembered["down_since"] is None:
            remembered["down_since"] = now
        confirmed = remembered["state"] in (None, "unavailable") or now - remembered["down_since"] >= UNAVAILABLE_GRACE_SECONDS
        state = "unavailable" if confirmed else remembered["state"]
    remembered["state"] = state
    return state, stale


def _poll(box, remembered):
    config = automation.scale_automation_settings()
    if config != remembered["config"]:
        box.state("config", config)
        remembered["config"] = config
    for _ in range(10):
        event = box.next()
        if event is None:
            break
        import_event(event)  # atomic decorator COMMITs before ack
        box.ack(event["id"])
    if time.time() - remembered["photo_recovery_at"] >= PHOTO_RECOVERY_INTERVAL_SECONDS:
        recover_collector_photos(box)
        remembered["photo_recovery_at"] = time.time()
    heartbeat = box.state("heartbeat") or {}
    state, stale = _collector_state(heartbeat, remembered)
    automation._store_runtime({
        "enabled": True, "state": state, "heartbeat_stale": stale,
        "last_checked_at": timezone.now().isoformat(), "active": None,
        "stable_weight_seconds": config["stable_weight_seconds"],
        "collector": {**box.counts(), "status": heartbeat.get("status", "starting")},
    })
    return automation.MonitorIteration(state=state)


def poll_once():
    remembered = _remembered(directory())
    try:
        return _poll(Outbox(directory()), remembered)
    except sqlite3.OperationalError as exc:
        if not is_busy(exc):
            raise
        # The collector holds the write lock for a moment (a photo blob, a WAL
        # checkpoint). Nothing was lost: unacknowledged events replay on the
        # next poll, and the last confirmed lane state stands meanwhile.
        state = remembered["state"] or "unavailable"
        if time.monotonic() - remembered["busy_logged_at"] >= BUSY_LOG_INTERVAL_SECONDS:
            remembered["busy_logged_at"] = time.monotonic()
            log.warning("Weighbridge outbox is locked by the collector; keeping state=%s until the next poll", state)
        return automation.MonitorIteration(state=state)
