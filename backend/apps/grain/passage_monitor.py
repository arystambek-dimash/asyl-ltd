"""Continuous physical polling with durable, independently leased OCR work."""

from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from . import passage_scale_automation as automation, scale
from .models import (
    AutomaticPassageCapture as Capture,
    PassageScaleAutomationState as Lane,
    UnassignedWeighing,
)
from .weighing_photos import link_available_photo, queue_photo


@transaction.atomic
def prepare_start():
    """An observation gap cannot prove continuity of the physical vehicle."""
    lane = (
        Lane.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if lane:
        Capture.objects.filter(
            status=Capture.PROCESSING, recognition_valid_until__isnull=True
        ).update(recognition_valid_until=timezone.now())
        automation._disarm_lane(lane)


@transaction.atomic
def _disable_pending():
    lane = (
        Lane.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if lane is None:
        return
    for capture in Capture.objects.select_for_update().filter(
        status=Capture.PROCESSING
    ):
        if capture.weight_kg is not None and capture.stable_weight_at is not None:
            UnassignedWeighing.objects.get_or_create(
                capture=capture,
                defaults={
                    "weight_kg": capture.weight_kg,
                    "stable_weight_at": capture.stable_weight_at,
                    "scale_number": capture.scale_number,
                    "scale_age_seconds": capture.scale_age_seconds,
                    "scale_updated_at": capture.scale_updated_at,
                    "camera": capture.camera,
                    "photo_request_id": automation._attempt_request_id(capture),
                    "vehicle_number": capture.vehicle_number,
                    "orientation": capture.orientation,
                    "reason": "automatic_scale_disabled",
                },
            )
        capture.status = Capture.FAILED
        capture.stage = Capture.DONE
        capture.retryable = False
        capture.requires_acknowledgement = False
        capture.error_code = "automatic_scale_disabled"
        capture.error_detail = "Автоматика выключена; сохранённый вес требует проверки."
        capture.completed_at = timezone.now()
        capture.save()
    automation._disarm_lane(lane)


@transaction.atomic
def _record_interrupted_candidate(code, detail):
    lane = (
        Lane.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if lane is None or lane.phase != Lane.STABILIZING:
        return
    Capture.objects.create(
        idempotency_key=uuid4(),
        camera=settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA,
        trigger_weight_kg=lane.candidate_weight_kg,
        status=Capture.FAILED,
        stage=Capture.DONE,
        error_code=code,
        error_detail=detail,
        requires_acknowledgement=False,
        completed_at=timezone.now(),
    )


@transaction.atomic
def _observation_gap(now):
    lane = (
        Lane.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if lane and lane.current_capture_id:
        Capture.objects.filter(
            pk=lane.current_capture_id, recognition_valid_until__isnull=True
        ).update(recognition_valid_until=now)
    _record_interrupted_candidate(
        "truck_scale_observation_lost",
        "Связь с весами прервалась до подтверждения веса.",
    )
    automation._discard_unobserved_candidate()


def poll_once(*, now=None):
    """Observe every physical edge; camera/photo requests never run here."""
    current = now or timezone.now()
    if not settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        _disable_pending()
        return automation.monitor_once(now=current)
    try:
        observation = scale.read_truck_scale_observation(scale.TRUCK_SCALE_KEY)
    except APIException:
        _observation_gap(current)
        automation._publish_runtime(now=current, unavailable=True)
        return automation.MonitorIteration(state="unavailable")
    unavailable = observation.state in {
        "disconnected",
        "unavailable",
        "stale",
        "malformed",
    }
    if unavailable:
        _observation_gap(current)
    if automation._is_empty(observation):
        _record_interrupted_candidate(
            "automatic_scale_not_stable",
            "Машина съехала до подтверждения стабильного веса.",
        )
    work = automation._advance_lane(observation, now=current, recover_work=False)
    capture = None
    if work is not None:
        capture = automation._capture_new_episode(work.capture_id, recognize=False)
    payload = automation._publish_runtime(now=timezone.now(), unavailable=unavailable)
    return automation.MonitorIteration(
        state=payload["state"],
        capture_id=capture.pk if capture else None,
    )


@transaction.atomic
def _claim_work(now):
    lane = (
        Lane.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if lane is None:
        return None
    # The database is the queue. Restarting a worker cannot lose a saved weight.
    for capture in (
        Capture.objects.select_for_update()
        .filter(status=Capture.PROCESSING)
        .order_by("id")[:100]
    ):
        if not capture.recognition_dispatched and capture.stage == Capture.RECOGNIZING:
            if (
                capture.departure_observed_at
                or capture.cleared_at
                or capture.recognition_valid_until
                or lane.current_capture_id != capture.pk
                or capture.stable_weight_at is None
                or now - capture.stable_weight_at > timedelta(seconds=5)
            ):
                automation._mark_plate_unresolved(
                    capture,
                    now=now,
                    code="vehicle_recognition_window_missed",
                    detail="Камера не начала обработку вовремя; вес сохранён для проверки.",
                )
                return automation._Work("apply", capture.pk)
            capture.recognition_dispatched = True
            capture.processing_started_at = now
            capture.save(
                update_fields=[
                    "recognition_dispatched",
                    "processing_started_at",
                    "updated_at",
                ]
            )
            return automation._Work("recognize", capture.pk)
        work = automation._claim_existing_work(lane, capture, now=now)
        if work:
            return work
    return None


def process_once():
    if not settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        return
    # Recover a crash between terminalizing OCR and exposing the saved weight
    # to the operator. Acknowledged historical failures were handled manually.
    failed = (
        Capture.objects.filter(
            status=Capture.FAILED,
            weight_kg__isnull=False,
            stable_weight_at__isnull=False,
            unassigned_weighing__isnull=True,
            acknowledged_at__isnull=True,
        )
        .order_by("id")
        .values_list("id", flat=True)
        .first()
    )
    if failed is not None:
        _preserve_failed_weight(failed)
    work = _claim_work(timezone.now())
    if work is None:
        return
    if work.kind == "recognize":
        capture = automation._recognize_capture(work.capture_id, retry_only=False)
    else:
        capture = automation._run_work(work, attach_photo_after=False)
    _preserve_failed_weight(capture.pk)
    link_available_photo(capture.camera, automation._attempt_request_id(capture))


@transaction.atomic
def _preserve_failed_weight(capture_id):
    Lane.objects.select_for_update().get(scale_number=scale.TRUCK_SCALE_KEY)
    capture = Capture.objects.select_for_update().get(pk=capture_id)
    if (
        capture.status != Capture.FAILED
        or capture.weight_kg is None
        or capture.stable_weight_at is None
    ):
        return
    UnassignedWeighing.objects.get_or_create(
        capture=capture,
        defaults={
            "weight_kg": capture.weight_kg,
            "stable_weight_at": capture.stable_weight_at,
            "scale_number": capture.scale_number,
            "scale_age_seconds": capture.scale_age_seconds,
            "scale_updated_at": capture.scale_updated_at,
            "camera": capture.camera,
            "photo_request_id": automation._attempt_request_id(capture),
            "vehicle_number": capture.vehicle_number,
            "orientation": capture.orientation,
            "reason": capture.error_code or "automatic_passage_apply_failed",
        },
    )
    capture.requires_acknowledgement = False
    capture.save(update_fields=["requires_acknowledgement", "updated_at"])
    queue_photo(
        capture.camera, automation._attempt_request_id(capture), capture=capture
    )
    link_available_photo(capture.camera, automation._attempt_request_id(capture))
