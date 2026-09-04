"""Durable automatic passage flow: scale edge -> OCR -> trip status.

The scale controller is pull-only, so this module deliberately implements a
fail-closed edge detector in PostgreSQL.  It starts unarmed, triggers once only
after confirmed empty -> stable occupied observations, and will not re-arm
until several fresh zero readings have been observed.  Missing, stale or
unstable data never counts as an empty scale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, timedelta
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, ValidationError

from apps.cameras import ai as camera_ai
from apps.cameras.models import VehiclePlateEvent
from apps.eventlog.services import log_event

from . import scale, services
from . import statuses as st
from .models import (
    PASSAGE_SCALE_DEFAULT_STABLE_WEIGHT_SECONDS,
    AutomaticPassageCapture,
    PassageScaleAutomationState,
    PassageWeightCapture,
)
from .weighing_photos import attach_photo
from .vehicle_weight_capture import (
    _api_exception_parts,
    _canonical_timestamp,
    _safe_ai_payload,
    _terminal_ai_error,
)

log = logging.getLogger(__name__)

RUNTIME_CACHE_KEY = "grain:passage-scale-automation:runtime:v1"
PUBLIC_RUNTIME_STATES = {
    "disabled",
    "idle",
    "candidate",
    "recognizing",
    "applying",
    "awaiting_clear",
    "manual_required",
    "unavailable",
}
_runtime_cache_read_failed = False
_runtime_cache_write_failed = False

# Recognition failures that a second OCR attempt cannot fix: the lane applies
# the weight without a plate right away instead of asking Camera-PC again.
RECOGNITION_FAILURES_WITHOUT_RETRY = frozenset(
    {
        "vehicle_recognition_auth_failed",
        "vehicle_recognition_not_configured",
        "vehicle_camera_not_configured",
        "vehicle_roi_unavailable",
        "vehicle_model_unavailable",
        "vehicle_recognition_idempotency_conflict",
    }
)


@dataclass(frozen=True, slots=True)
class MonitorIteration:
    state: str
    capture_id: int | None = None
    action: str = ""
    wagon_id: int | None = None
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class _Work:
    kind: str
    capture_id: int


class _CaptureRejected(Exception):
    def __init__(self, code: str, detail: str, *, status_code: int = 409) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _processing_lease(stage: str) -> timedelta:
    if stage == AutomaticPassageCapture.RECOGNIZING:
        timeout = float(settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS)
        return timedelta(seconds=max(90.0, timeout * 3.0 + 15.0))
    timeout = float(settings.TRUCK_SCALE_TIMEOUT_SECONDS)
    return timedelta(seconds=max(10.0, timeout + 5.0))


def _retry_delay() -> timedelta:
    return timedelta(
        seconds=max(1.0, float(settings.VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS))
    )


def _is_empty(observation: scale.ScaleObservation) -> bool:
    return (
        observation.state == "ready"
        and observation.weight_kg is not None
        and observation.weight_kg
        <= Decimal(settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG)
    )


def _is_occupied(observation: scale.ScaleObservation) -> bool:
    return (
        observation.state == "ready"
        and observation.weight_kg is not None
        and observation.weight_kg
        > Decimal(settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG)
    )


def _reset_candidate(state: PassageScaleAutomationState, *, phase: str) -> None:
    state.phase = phase
    state.stable_streak = 0
    state.stability_started_at = None
    state.candidate_weight_kg = None


def _save_state(state: PassageScaleAutomationState, *fields: str) -> None:
    state.save(update_fields=[*fields, "updated_at"])


@transaction.atomic
def _discard_unobserved_candidate() -> None:
    """Require a new full interval after a scale observation outage."""

    state = (
        PassageScaleAutomationState.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if state is None or state.phase != PassageScaleAutomationState.STABILIZING:
        return
    _reset_candidate(state, phase=PassageScaleAutomationState.ARMED)
    _save_state(
        state,
        "phase",
        "stable_streak",
        "stability_started_at",
        "candidate_weight_kg",
    )


def _rearm_lane(state: PassageScaleAutomationState) -> None:
    state.phase = PassageScaleAutomationState.ARMED
    state.clear_streak = 0
    state.stable_streak = 0
    state.stability_started_at = None
    state.candidate_weight_kg = None
    state.current_capture = None
    _save_state(
        state,
        "phase",
        "clear_streak",
        "stable_streak",
        "stability_started_at",
        "candidate_weight_kg",
        "current_capture",
    )


def _mark_interrupted_claim(
    state: PassageScaleAutomationState,
    capture: AutomaticPassageCapture,
    *,
    now,
) -> None:
    capture.status = AutomaticPassageCapture.FAILED
    capture.stage = AutomaticPassageCapture.DONE
    capture.retryable = False
    capture.response_status = 409
    capture.error_code = "automatic_scale_capture_interrupted"
    capture.error_detail = (
        "Чтение весов прервалось до сохранения показания; вес не записан."
    )
    # Nothing was recorded, so a fresh clear edge is all the lane needs.
    capture.requires_acknowledgement = False
    capture.processing_started_at = None
    capture.completed_at = now
    capture.save(
        update_fields=[
            "status",
            "stage",
            "retryable",
            "requires_acknowledgement",
            "response_status",
            "error_code",
            "error_detail",
            "processing_started_at",
            "completed_at",
            "updated_at",
        ]
    )
    state.phase = PassageScaleAutomationState.AWAITING_CLEAR
    state.clear_streak = 0
    _save_state(state, "phase", "clear_streak")


def _claim_existing_work(
    state: PassageScaleAutomationState,
    capture: AutomaticPassageCapture,
    *,
    now,
) -> _Work | None:
    if capture.status != AutomaticPassageCapture.PROCESSING:
        if state.phase != PassageScaleAutomationState.AWAITING_CLEAR:
            state.phase = PassageScaleAutomationState.AWAITING_CLEAR
            state.clear_streak = 0
            _save_state(state, "phase", "clear_streak")
        return None

    if capture.stage == AutomaticPassageCapture.CLAIMED:
        lease_start = capture.processing_started_at or capture.updated_at
        if lease_start >= now - _processing_lease(capture.stage):
            return None
        _mark_interrupted_claim(state, capture, now=now)
        return None

    if capture.stage not in {
        AutomaticPassageCapture.RECOGNIZING,
        AutomaticPassageCapture.APPLYING,
    }:
        _mark_interrupted_claim(state, capture, now=now)
        return None

    if capture.retryable:
        if capture.updated_at > now - _retry_delay():
            return None
    else:
        lease_start = capture.processing_started_at or capture.updated_at
        if lease_start >= now - _processing_lease(capture.stage):
            return None

    if (
        capture.stage == AutomaticPassageCapture.RECOGNIZING
        and capture.needs_new_attempt
    ):
        # Camera-PC gave a terminal answer for the previous attempt; ask again
        # with a fresh scale read and a new UUID while the truck still stands.
        capture.needs_new_attempt = False
        capture.retryable = False
        capture.processing_started_at = now
        capture.error_code = ""
        capture.error_detail = ""
        capture.response_status = None
        capture.save(
            update_fields=[
                "needs_new_attempt",
                "retryable",
                "processing_started_at",
                "error_code",
                "error_detail",
                "response_status",
                "updated_at",
            ]
        )
        return _Work("recognize_again", capture.pk)

    was_retryable = capture.retryable
    if (
        capture.stage == AutomaticPassageCapture.RECOGNIZING
        and was_retryable
        and capture.recognition_attempts
        >= settings.VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS
        and capture.final_lookup_attempted
    ):
        # The last allowed camera call never produced a plate. The weight is
        # still a real weighing: apply it without a number.
        _mark_plate_unresolved(
            capture,
            now=now,
            code="vehicle_recognition_attempts_exhausted",
            detail="Номер не распознан после допустимых повторов.",
        )
        return _Work("apply", capture.pk)

    capture.retryable = False
    capture.processing_started_at = now
    capture.error_code = ""
    capture.error_detail = ""
    capture.response_status = None
    update_fields = [
        "retryable",
        "processing_started_at",
        "error_code",
        "error_detail",
        "response_status",
        "updated_at",
    ]
    if capture.stage == AutomaticPassageCapture.RECOGNIZING:
        if (
            was_retryable
            and capture.recognition_attempts
            < settings.VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS
        ):
            capture.recognition_attempts += 1
            update_fields.append("recognition_attempts")
        elif (
            capture.recognition_attempts
            >= settings.VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS
            and not capture.final_lookup_attempted
        ):
            # A timeout on the last normal call still has an unknown remote
            # outcome. Permit one separately recorded lookup-only request.
            capture.final_lookup_attempted = True
            update_fields.append("final_lookup_attempted")
    capture.save(update_fields=update_fields)
    return _Work(
        "recognize_retry"
        if capture.stage == AutomaticPassageCapture.RECOGNIZING
        else "apply",
        capture.pk,
    )


def _mark_plate_unresolved(
    capture: AutomaticPassageCapture,
    *,
    now,
    code: str,
    detail: str,
) -> None:
    capture.plate_unresolved = True
    capture.needs_new_attempt = False
    capture.stage = AutomaticPassageCapture.APPLYING
    capture.retryable = False
    capture.response_status = None
    capture.error_code = code[:64]
    capture.error_detail = detail[:300]
    capture.processing_started_at = now
    capture.save(
        update_fields=[
            "plate_unresolved",
            "needs_new_attempt",
            "stage",
            "retryable",
            "response_status",
            "error_code",
            "error_detail",
            "processing_started_at",
            "updated_at",
        ]
    )


@transaction.atomic
def _claim_pending_work(*, now) -> tuple[bool, _Work | None]:
    """Resume persisted OCR/apply work before depending on another scale poll."""

    state = (
        PassageScaleAutomationState.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if state is None or state.phase != PassageScaleAutomationState.PROCESSING:
        return False, None
    if state.current_capture_id is None:
        state.phase = PassageScaleAutomationState.UNARMED
        state.clear_streak = 0
        state.stable_streak = 0
        state.stability_started_at = None
        state.candidate_weight_kg = None
        _save_state(
            state,
            "phase",
            "clear_streak",
            "stable_streak",
            "stability_started_at",
            "candidate_weight_kg",
        )
        return True, None
    capture = AutomaticPassageCapture.objects.select_for_update().get(
        pk=state.current_capture_id
    )
    work = _claim_existing_work(state, capture, now=now)
    # This iteration is exclusively recovery, even when recovery terminalizes
    # the capture. Do not immediately touch hardware or advance another lane
    # phase in the same second.
    return True, work


def _disarm_lane(state: PassageScaleAutomationState) -> None:
    state.phase = PassageScaleAutomationState.UNARMED
    state.clear_streak = 0
    state.stable_streak = 0
    state.stability_started_at = None
    state.candidate_weight_kg = None
    state.current_capture = None
    _save_state(
        state,
        "phase",
        "clear_streak",
        "stable_streak",
        "stability_started_at",
        "candidate_weight_kg",
        "current_capture",
    )


def _reset_idle_lane(state: PassageScaleAutomationState) -> None:
    if state.phase not in {
        PassageScaleAutomationState.UNARMED,
        PassageScaleAutomationState.ARMED,
        PassageScaleAutomationState.STABILIZING,
    }:
        return
    if (
        state.phase == PassageScaleAutomationState.UNARMED
        and state.clear_streak == 0
        and state.stable_streak == 0
        and state.stability_started_at is None
        and state.candidate_weight_kg is None
        and state.current_capture_id is None
    ):
        return
    _disarm_lane(state)


@transaction.atomic
def prepare_monitor_start() -> None:
    """Fence idle scale edges that may have changed while the process was down."""

    state = (
        PassageScaleAutomationState.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if state is not None:
        _reset_idle_lane(state)


@transaction.atomic
def _prepare_disabled_lane(*, now) -> None:
    """Cancel resumable work before operators switch to the manual path."""

    state = (
        PassageScaleAutomationState.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    if state is None:
        return
    if state.phase in {
        PassageScaleAutomationState.UNARMED,
        PassageScaleAutomationState.ARMED,
        PassageScaleAutomationState.STABILIZING,
    }:
        _reset_idle_lane(state)
        return
    if state.phase == PassageScaleAutomationState.AWAITING_CLEAR:
        capture = (
            AutomaticPassageCapture.objects.select_for_update().get(
                pk=state.current_capture_id
            )
            if state.current_capture_id is not None
            else None
        )
        unresolved_failure = capture is not None and capture.needs_operator
        if not unresolved_failure:
            # New automated work is disabled, so a terminal/acknowledged
            # episode must not keep manual controls locked forever. Re-enable
            # starts UNARMED and still requires a new confirmed clear streak.
            _disarm_lane(state)
        return
    if state.phase != PassageScaleAutomationState.PROCESSING:
        return
    if state.current_capture_id is None:
        state.phase = PassageScaleAutomationState.UNARMED
        state.clear_streak = 0
        state.stable_streak = 0
        state.stability_started_at = None
        state.candidate_weight_kg = None
        _save_state(
            state,
            "phase",
            "clear_streak",
            "stable_streak",
            "stability_started_at",
            "candidate_weight_kg",
        )
        return

    capture = AutomaticPassageCapture.objects.select_for_update().get(
        pk=state.current_capture_id
    )
    if capture.status == AutomaticPassageCapture.PROCESSING:
        capture.status = AutomaticPassageCapture.FAILED
        capture.stage = AutomaticPassageCapture.DONE
        capture.retryable = False
        capture.response_status = 409
        capture.error_code = "automatic_scale_disabled"
        capture.error_detail = (
            "Автоматические весы выключены; завершите операцию вручную."
        )
        capture.processing_started_at = None
        capture.completed_at = now
        capture.save(
            update_fields=[
                "status",
                "stage",
                "retryable",
                "response_status",
                "error_code",
                "error_detail",
                "processing_started_at",
                "completed_at",
                "updated_at",
            ]
        )
    state.phase = PassageScaleAutomationState.AWAITING_CLEAR
    state.clear_streak = 0
    state.stable_streak = 0
    state.stability_started_at = None
    state.candidate_weight_kg = None
    _save_state(
        state,
        "phase",
        "clear_streak",
        "stable_streak",
        "stability_started_at",
        "candidate_weight_kg",
    )


@transaction.atomic
def _advance_lane(
    observation: scale.ScaleObservation,
    *,
    now,
) -> _Work | None:
    state, _created = (
        PassageScaleAutomationState.objects.select_for_update().get_or_create(
            scale_number=scale.TRUCK_SCALE_KEY,
            defaults={"phase": PassageScaleAutomationState.UNARMED},
        )
    )
    capture = None
    if state.current_capture_id is not None:
        capture = AutomaticPassageCapture.objects.select_for_update().get(
            pk=state.current_capture_id
        )

    manual_capture_pending = PassageWeightCapture.objects.filter(
        status=PassageWeightCapture.PROCESSING,
    ).exists()
    if manual_capture_pending and state.phase in {
        PassageScaleAutomationState.UNARMED,
        PassageScaleAutomationState.ARMED,
        PassageScaleAutomationState.STABILIZING,
    }:
        # An in-flight weight-first request reserves this physical lane until
        # its weight is stored/failed. A manually registered passage without a
        # weight does not: automation weighs it when its plate is recognized.
        _reset_idle_lane(state)
        return None

    if state.phase == PassageScaleAutomationState.PROCESSING:
        if capture is None:
            state.phase = PassageScaleAutomationState.UNARMED
            state.clear_streak = 0
            state.stable_streak = 0
            state.stability_started_at = None
            state.candidate_weight_kg = None
            _save_state(
                state,
                "phase",
                "clear_streak",
                "stable_streak",
                "stability_started_at",
                "candidate_weight_kg",
            )
            return None
        work = _claim_existing_work(state, capture, now=now)
        if state.phase == PassageScaleAutomationState.PROCESSING:
            return work

    if state.phase == PassageScaleAutomationState.AWAITING_CLEAR:
        unresolved_failure = capture is not None and capture.needs_operator
        if (
            capture is not None
            and unresolved_failure
            and capture.cleared_at is not None
        ):
            # Physical clearing and operator resolution are independent. Keep
            # the lane visibly blocked until a human acknowledges the failed
            # operation, even if the vehicle has already driven away.
            return None
        if not _is_empty(observation):
            if state.clear_streak:
                state.clear_streak = 0
                _save_state(state, "clear_streak")
            return None
        state.clear_streak += 1
        if state.clear_streak < settings.VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS:
            _save_state(state, "clear_streak")
            return None
        if capture is not None and capture.cleared_at is None:
            capture.cleared_at = now
            capture.save(update_fields=["cleared_at", "updated_at"])
        if capture is not None and unresolved_failure:
            state.clear_streak = 0
            _save_state(state, "clear_streak")
            log.warning(
                "Automatic passage scale remains blocked for acknowledgement "
                "capture_id=%s",
                capture.pk,
            )
            return None
        _rearm_lane(state)
        log.info("Automatic passage scale re-armed after confirmed clear")
        return None

    if state.phase == PassageScaleAutomationState.UNARMED:
        if not _is_empty(observation):
            if state.clear_streak:
                state.clear_streak = 0
                _save_state(state, "clear_streak")
            return None
        state.clear_streak += 1
        if state.clear_streak >= settings.VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS:
            state.phase = PassageScaleAutomationState.ARMED
            state.clear_streak = 0
            _save_state(state, "phase", "clear_streak")
            log.info("Automatic passage scale armed after safe startup clear")
        else:
            _save_state(state, "clear_streak")
        return None

    if state.phase == PassageScaleAutomationState.ARMED:
        if not _is_occupied(observation):
            return None
        state.phase = PassageScaleAutomationState.STABILIZING
        state.stable_streak = 1
        state.stability_started_at = now
        state.candidate_weight_kg = observation.weight_kg
        _save_state(
            state,
            "phase",
            "stable_streak",
            "stability_started_at",
            "candidate_weight_kg",
        )
        return None

    elif state.phase == PassageScaleAutomationState.STABILIZING:
        if not _is_occupied(observation):
            _reset_candidate(state, phase=PassageScaleAutomationState.ARMED)
            _save_state(
                state,
                "phase",
                "stable_streak",
                "stability_started_at",
                "candidate_weight_kg",
            )
            return None
        tolerance = Decimal(settings.VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG)
        candidate = state.candidate_weight_kg
        started_at = state.stability_started_at
        if (
            candidate is None
            or started_at is None
            or now < started_at
            or abs(observation.weight_kg - candidate) > tolerance
        ):
            state.stable_streak = 1
            state.stability_started_at = now
            state.candidate_weight_kg = observation.weight_kg
            _save_state(
                state,
                "stable_streak",
                "stability_started_at",
                "candidate_weight_kg",
            )
            return None
        state.stable_streak += 1
        stable_for = (now - started_at).total_seconds()
        if stable_for < state.stable_weight_seconds or state.stable_streak < 2:
            _save_state(state, "stable_streak")
            return None

    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        camera=settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA,
        trigger_weight_kg=observation.weight_kg,
        processing_started_at=now,
    )
    state.phase = PassageScaleAutomationState.PROCESSING
    state.clear_streak = 0
    state.stable_streak = 0
    state.stability_started_at = None
    state.candidate_weight_kg = None
    state.current_capture = capture
    _save_state(
        state,
        "phase",
        "clear_streak",
        "stable_streak",
        "stability_started_at",
        "candidate_weight_kg",
        "current_capture",
    )
    log.info("Claimed automatic passage scale episode capture_id=%s", capture.pk)
    return _Work("capture", capture.pk)


def _reading_from_capture(capture: AutomaticPassageCapture) -> scale.ScaleReading:
    if (
        capture.weight_kg is None
        or capture.scale_age_seconds is None
        or capture.stable_weight_at is None
    ):
        raise _CaptureRejected(
            "automatic_scale_sample_missing",
            "Сохранённое показание весов неполное.",
        )
    return scale.ScaleReading(
        weight_kg=Decimal(capture.weight_kg),
        age_seconds=capture.scale_age_seconds,
        updated_at=capture.scale_updated_at or None,
    )


@transaction.atomic
def _persist_scale_sample(
    capture_id: int,
    reading: scale.ScaleReading,
    *,
    stable_weight_at,
) -> AutomaticPassageCapture:
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if (
        capture.status != AutomaticPassageCapture.PROCESSING
        or capture.stage != AutomaticPassageCapture.CLAIMED
    ):
        raise _CaptureRejected(
            "automatic_scale_state_changed",
            "Состояние автоматического взвешивания изменилось.",
        )
    weight_kg = services._whole_scale_weight_kg(reading)
    if weight_kg <= settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG:
        raise _CaptureRejected(
            "automatic_scale_candidate_cleared",
            "Машина покинула весы до контрольного чтения.",
        )
    if capture.trigger_weight_kg is None or abs(
        reading.weight_kg - capture.trigger_weight_kg
    ) > Decimal(settings.VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG):
        raise _CaptureRejected(
            "automatic_scale_candidate_changed",
            "Вес изменился до контрольного чтения; автоматическая операция остановлена.",
        )
    capture.weight_kg = weight_kg
    capture.scale_age_seconds = reading.age_seconds
    capture.scale_updated_at = reading.updated_at or ""
    capture.stable_weight_at = stable_weight_at
    capture.attempt_request_id = capture.idempotency_key
    capture.attempt_stable_weight_at = stable_weight_at
    capture.stage = AutomaticPassageCapture.RECOGNIZING
    capture.recognition_attempts = 1
    capture.processing_started_at = timezone.now()
    capture.save(
        update_fields=[
            "weight_kg",
            "scale_age_seconds",
            "scale_updated_at",
            "stable_weight_at",
            "attempt_request_id",
            "attempt_stable_weight_at",
            "stage",
            "recognition_attempts",
            "processing_started_at",
            "updated_at",
        ]
    )
    return capture


def _attempt_request_id(capture: AutomaticPassageCapture) -> UUID:
    return capture.attempt_request_id or capture.idempotency_key


def _attempt_stable_weight_at(capture: AutomaticPassageCapture):
    return capture.attempt_stable_weight_at or capture.stable_weight_at


@transaction.atomic
def _persist_next_attempt(
    capture_id: int,
    reading: scale.ScaleReading,
    *,
    stable_weight_at,
) -> AutomaticPassageCapture:
    """Bind a fresh strict read to the next Camera-PC attempt.

    If the truck has left or a different weight is now on the scale, the
    original sample is applied without a plate instead of asking the camera
    about a vehicle that is no longer the one we weighed.
    """

    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if (
        capture.status != AutomaticPassageCapture.PROCESSING
        or capture.stage != AutomaticPassageCapture.RECOGNIZING
    ):
        raise _CaptureRejected(
            "automatic_scale_state_changed",
            "Состояние автоматического взвешивания изменилось.",
        )
    now = timezone.now()
    tolerance = Decimal(settings.VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG)
    if capture.weight_kg is None or abs(
        reading.weight_kg - Decimal(capture.weight_kg)
    ) > tolerance:
        _mark_plate_unresolved(
            capture,
            now=now,
            code="vehicle_recognition_vehicle_left",
            detail="Машина уехала до повторного распознавания; вес записан без номера.",
        )
        return capture
    attempt = capture.recognition_attempts + 1
    capture.recognition_attempts = attempt
    capture.attempt_request_id = uuid5(capture.idempotency_key, f"attempt-{attempt}")
    capture.attempt_stable_weight_at = stable_weight_at
    capture.processing_started_at = now
    capture.save(
        update_fields=[
            "recognition_attempts",
            "attempt_request_id",
            "attempt_stable_weight_at",
            "processing_started_at",
            "updated_at",
        ]
    )
    return capture


@transaction.atomic
def _persist_recognition(
    capture_id: int,
    payload: dict,
) -> AutomaticPassageCapture:
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if capture.status != AutomaticPassageCapture.PROCESSING:
        raise _CaptureRejected(
            "automatic_scale_state_changed",
            "Операция распознавания уже завершена.",
        )
    if capture.stage == AutomaticPassageCapture.APPLYING:
        return capture
    if capture.stage != AutomaticPassageCapture.RECOGNIZING:
        raise _CaptureRejected(
            "automatic_scale_state_changed",
            "Операция не ожидает результат распознавания.",
        )

    recognized_at = parse_datetime(str(payload.get("recognized_at") or ""))
    response_trigger = parse_datetime(str(payload.get("stable_weight_at") or ""))
    expected_trigger = _attempt_stable_weight_at(capture)
    if (
        recognized_at is None
        or timezone.is_naive(recognized_at)
        or response_trigger is None
        or timezone.is_naive(response_trigger)
        or expected_trigger is None
        or response_trigger != expected_trigger
    ):
        raise _CaptureRejected(
            "vehicle_recognition_malformed",
            "Camera-PC вернул некорректные временные метки.",
            status_code=502,
        )

    confirmation = payload["confirmation"]
    safe_payload = _safe_ai_payload(payload)
    event_defaults = {
        "vehicle_number": str(payload["vehicle_number"]),
        "camera": capture.camera,
        "source": str(payload["source"]),
        "detected_at": capture.stable_weight_at,
        "stationary_seconds": Decimal(0),
        "confirmation_votes": int(confirmation["votes"]),
        "detector_confidence": Decimal(str(confirmation["detector_confidence"])),
        "ocr_confidence": Decimal(str(confirmation["ocr_confidence"])),
        "payload_json": safe_payload,
    }
    event, created = VehiclePlateEvent.objects.get_or_create(
        event_id=capture.idempotency_key,
        defaults=event_defaults,
    )
    if not created and any(
        (
            event.vehicle_number != event_defaults["vehicle_number"],
            event.camera != event_defaults["camera"],
            event.source != event_defaults["source"],
            event.detected_at != event_defaults["detected_at"],
        )
    ):
        raise _CaptureRejected(
            "vehicle_recognition_idempotency_conflict",
            "Идентификатор распознавания уже принадлежит другому событию.",
        )

    capture.camera_source = str(payload["source"])
    capture.vehicle_number = str(payload["vehicle_number"])
    capture.recognized_at = recognized_at
    capture.confirmation_votes = int(confirmation["votes"])
    capture.detector_confidence = Decimal(str(confirmation["detector_confidence"]))
    capture.ocr_confidence = Decimal(str(confirmation["ocr_confidence"]))
    capture.ai_payload_json = safe_payload
    capture.vehicle_plate_event = event
    capture.stage = AutomaticPassageCapture.APPLYING
    capture.response_status = 200
    capture.retryable = False
    capture.error_code = ""
    capture.error_detail = ""
    capture.processing_started_at = timezone.now()
    capture.save(
        update_fields=[
            "camera_source",
            "vehicle_number",
            "recognized_at",
            "confirmation_votes",
            "detector_confidence",
            "ocr_confidence",
            "ai_payload_json",
            "vehicle_plate_event",
            "stage",
            "response_status",
            "retryable",
            "error_code",
            "error_detail",
            "processing_started_at",
            "updated_at",
        ]
    )
    return capture


@transaction.atomic
def _finish_error(
    capture_id: int,
    *,
    status_code: int,
    code: str,
    detail: str,
    retryable: bool,
    ai_payload: dict | None = None,
    requires_acknowledgement: bool = True,
) -> AutomaticPassageCapture:
    state = PassageScaleAutomationState.objects.select_for_update().get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if capture.status != AutomaticPassageCapture.PROCESSING:
        return capture

    may_retry = retryable and (
        capture.stage == AutomaticPassageCapture.APPLYING
        or (
            capture.stage == AutomaticPassageCapture.RECOGNIZING
            and (
                capture.recognition_attempts
                < settings.VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS
                or not capture.final_lookup_attempted
            )
        )
    )
    capture.retryable = may_retry
    capture.response_status = status_code
    capture.error_code = code[:64]
    capture.error_detail = detail[:300]
    capture.processing_started_at = None
    update_fields = [
        "retryable",
        "response_status",
        "error_code",
        "error_detail",
        "processing_started_at",
        "updated_at",
    ]
    if ai_payload is not None:
        capture.ai_payload_json = _safe_ai_payload(ai_payload)
        update_fields.append("ai_payload_json")
    if not may_retry:
        capture.status = AutomaticPassageCapture.FAILED
        capture.stage = AutomaticPassageCapture.DONE
        capture.requires_acknowledgement = requires_acknowledgement
        capture.completed_at = timezone.now()
        update_fields.extend(
            ["status", "stage", "requires_acknowledgement", "completed_at"]
        )
        state.phase = PassageScaleAutomationState.AWAITING_CLEAR
        state.clear_streak = 0
        _save_state(state, "phase", "clear_streak")
    capture.save(update_fields=update_fields)
    return capture


@transaction.atomic
def _resolve_recognition_failure(
    capture_id: int,
    *,
    status_code: int,
    code: str,
    detail: str,
    ai_payload: dict | None = None,
) -> AutomaticPassageCapture:
    """Decide what a terminal Camera-PC answer without a plate means.

    While attempts remain and the failure is not a configuration problem, the
    capture waits for another OCR attempt against a fresh scale read. When the
    camera cannot help any more, the weighing is applied without a plate. The
    lane is never latched for an operator here.
    """

    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if (
        capture.status != AutomaticPassageCapture.PROCESSING
        or capture.stage != AutomaticPassageCapture.RECOGNIZING
    ):
        return capture
    if ai_payload is not None:
        capture.ai_payload_json = _safe_ai_payload(ai_payload)
    now = timezone.now()
    may_try_again = (
        code not in RECOGNITION_FAILURES_WITHOUT_RETRY
        and capture.recognition_attempts
        < settings.VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS
    )
    if may_try_again:
        capture.needs_new_attempt = True
        capture.retryable = True
        capture.response_status = status_code
        capture.error_code = code[:64]
        capture.error_detail = detail[:300]
        capture.processing_started_at = None
        capture.save(
            update_fields=[
                "ai_payload_json",
                "needs_new_attempt",
                "retryable",
                "response_status",
                "error_code",
                "error_detail",
                "processing_started_at",
                "updated_at",
            ]
        )
        return capture
    capture.save(update_fields=["ai_payload_json", "updated_at"])
    _mark_plate_unresolved(capture, now=now, code=code, detail=detail)
    return capture


@transaction.atomic
def _finish_success(
    capture_id: int,
    *,
    result: services.VehiclePlateAutomationResult,
) -> AutomaticPassageCapture:
    state = PassageScaleAutomationState.objects.select_for_update().get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if capture.status != AutomaticPassageCapture.PROCESSING:
        return capture
    capture.status = AutomaticPassageCapture.COMPLETED
    capture.stage = AutomaticPassageCapture.DONE
    capture.action = result.action
    capture.wagon_id = result.wagon_id
    capture.retryable = False
    capture.response_status = 200
    if not capture.plate_unresolved:
        # A plate-less completion keeps the last recognition failure as the
        # audit explanation of why the trip has no number.
        capture.error_code = ""
        capture.error_detail = ""
    capture.processing_started_at = None
    capture.completed_at = timezone.now()
    capture.save(
        update_fields=[
            "status",
            "stage",
            "action",
            "wagon",
            "retryable",
            "response_status",
            "error_code",
            "error_detail",
            "processing_started_at",
            "completed_at",
            "updated_at",
        ]
    )
    state.phase = PassageScaleAutomationState.AWAITING_CLEAR
    state.clear_streak = 0
    _save_state(state, "phase", "clear_streak")
    log.info(
        "Automatic passage scale applied capture_id=%s action=%s wagon_id=%s",
        capture.pk,
        capture.action,
        capture.wagon_id,
    )
    return capture


@transaction.atomic
def _apply_recognized_capture(capture_id: int) -> AutomaticPassageCapture:
    # Applying the durable weight/OCR pair is the only step that mutates the
    # passage business state.  Keep the shared lane mutex through that DB-only
    # operation and its terminal capture update so disabling automation or a
    # manual mutation can never interleave between "applied" and "completed".
    scale.configure_authoritative_db_timeouts()
    state = (
        PassageScaleAutomationState.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if (
        state is None
        or state.phase != PassageScaleAutomationState.PROCESSING
        or state.current_capture_id != capture.pk
        or capture.status != AutomaticPassageCapture.PROCESSING
        or capture.stage != AutomaticPassageCapture.APPLYING
    ):
        return capture
    try:
        reading = _reading_from_capture(capture)
        if capture.plate_unresolved:
            result = services.apply_unidentified_passage_scale_sample(
                reading=reading,
                camera=capture.camera,
                request_id=_attempt_request_id(capture),
                stable_weight_at=capture.stable_weight_at,
                capture=capture,
            )
        else:
            if capture.vehicle_plate_event_id is None:
                raise _CaptureRejected(
                    "automatic_scale_event_missing",
                    "Событие распознавания не сохранено.",
                )
            result = services.apply_automatic_passage_scale_sample(
                capture.vehicle_plate_event_id,
                reading=reading,
                photo_request_id=_attempt_request_id(capture),
                photo_camera=capture.camera,
            )
    except _CaptureRejected as error:
        return _finish_error(
            capture_id,
            status_code=error.status_code,
            code=error.code,
            detail=error.detail,
            retryable=False,
        )
    except APIException as error:
        detail, code = _api_exception_parts(error)
        return _finish_error(
            capture_id,
            status_code=int(error.status_code),
            code=code,
            detail=detail,
            retryable=int(error.status_code) >= 500,
        )

    if result.status in {"processed", "already_processed"}:
        if (
            result.action not in {
                services.AUTO_ACTION_ENTRY,
                services.AUTO_ACTION_EXIT,
                services.AUTO_ACTION_UNASSIGNED,
            }
            or (
                result.wagon_id is None
                and result.action != services.AUTO_ACTION_UNASSIGNED
            )
        ):
            return _finish_error(
                capture_id,
                status_code=409,
                code="automatic_passage_apply_state_changed",
                detail=(
                    "Сохранённое событие номера принадлежит другой операции; "
                    "нужен оператор."
                ),
                retryable=False,
            )
        return _finish_success(capture_id, result=result)
    return _finish_error(
        capture_id,
        status_code=503 if result.retryable else 409,
        code=result.error or "automatic_passage_apply_failed",
        detail=(
            "Сохранение рейса временно занято."
            if result.retryable
            else "Автоматическое оформление остановлено; нужен оператор."
        ),
        retryable=result.retryable,
    )


def _after_recognition_failure(capture: AutomaticPassageCapture) -> AutomaticPassageCapture:
    """Apply immediately when the failure was resolved into a plate-less apply."""

    if (
        capture.status == AutomaticPassageCapture.PROCESSING
        and capture.stage == AutomaticPassageCapture.APPLYING
    ):
        return _apply_recognized_capture(capture.pk)
    return capture


def _recognize_capture(
    capture_id: int,
    *,
    retry_only: bool,
) -> AutomaticPassageCapture:
    capture = AutomaticPassageCapture.objects.get(pk=capture_id)
    if capture.stage == AutomaticPassageCapture.APPLYING:
        return _apply_recognized_capture(capture_id)
    try:
        trigger = _attempt_stable_weight_at(capture)
        if trigger is None:
            raise _CaptureRejected(
                "automatic_scale_sample_missing",
                "Время стабильного веса не сохранено.",
            )
        recognize = (
            camera_ai.retry_vehicle_recognition_from_camera
            if retry_only
            else camera_ai.recognize_vehicle_from_camera
        )
        payload = recognize(
            capture.camera,
            _attempt_request_id(capture),
            _canonical_timestamp(trigger),
        )
        _persist_recognition(capture_id, payload)
    except _CaptureRejected as error:
        # The stored sample is unusable (or the state moved on): nothing to
        # apply, nothing for an operator to repair.
        return _finish_error(
            capture_id,
            status_code=error.status_code,
            code=error.code,
            detail=error.detail,
            retryable=False,
            requires_acknowledgement=False,
        )
    except camera_ai.AiProtocolError:
        return _after_recognition_failure(
            _resolve_recognition_failure(
                capture_id,
                status_code=502,
                code="vehicle_recognition_malformed",
                detail="Camera-PC вернул некорректный результат распознавания.",
            )
        )
    except camera_ai.AiUnavailable:
        return _finish_error(
            capture_id,
            status_code=503,
            code="vehicle_recognition_unavailable",
            detail="Связь с Camera-PC прервалась; сохранённый запрос будет повторён.",
            retryable=True,
        )
    except camera_ai.AiError as error:
        status_code, code, detail, retryable = _terminal_ai_error(error)
        if retryable:
            return _finish_error(
                capture_id,
                status_code=status_code,
                code=code,
                detail=detail,
                retryable=True,
                ai_payload=error.payload,
            )
        return _after_recognition_failure(
            _resolve_recognition_failure(
                capture_id,
                status_code=status_code,
                code=code,
                detail=detail,
                ai_payload=error.payload,
            )
        )
    return _apply_recognized_capture(capture_id)


def _recognize_again(capture_id: int) -> AutomaticPassageCapture:
    """Second and later OCR attempts for a truck still standing on the scale."""

    capture = AutomaticPassageCapture.objects.get(pk=capture_id)
    if capture.stage == AutomaticPassageCapture.APPLYING:
        return _apply_recognized_capture(capture_id)
    try:
        with scale.authoritative_capture(scale.TRUCK_SCALE_KEY):
            read_started_at = timezone.now()
            reading = scale.read_truck_scale(scale.TRUCK_SCALE_KEY)
            stable_weight_at = read_started_at - timedelta(
                seconds=float(reading.age_seconds)
            )
            capture = _persist_next_attempt(
                capture_id,
                reading,
                stable_weight_at=stable_weight_at,
            )
            if capture.stage == AutomaticPassageCapture.APPLYING:
                return _apply_recognized_capture(capture_id)
            return _recognize_capture(capture_id, retry_only=False)
    except _CaptureRejected as error:
        return _finish_error(
            capture_id,
            status_code=error.status_code,
            code=error.code,
            detail=error.detail,
            retryable=False,
            requires_acknowledgement=False,
        )
    except APIException as error:
        # The scale could not confirm the truck is still there. Each failed
        # look costs one attempt so a dead scale cannot spin forever; once
        # attempts run out the original sample is applied without a plate.
        detail, code = _api_exception_parts(error)
        return _after_recognition_failure(
            _defer_next_attempt(capture_id, status_code=int(error.status_code), code=code, detail=detail)
        )


@transaction.atomic
def _defer_next_attempt(
    capture_id: int,
    *,
    status_code: int,
    code: str,
    detail: str,
) -> AutomaticPassageCapture:
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if (
        capture.status != AutomaticPassageCapture.PROCESSING
        or capture.stage != AutomaticPassageCapture.RECOGNIZING
    ):
        return capture
    now = timezone.now()
    capture.recognition_attempts += 1
    if (
        capture.recognition_attempts
        >= settings.VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS
    ):
        capture.save(update_fields=["recognition_attempts", "updated_at"])
        _mark_plate_unresolved(capture, now=now, code=code, detail=detail)
        return capture
    capture.needs_new_attempt = True
    capture.retryable = True
    capture.response_status = status_code
    capture.error_code = code[:64]
    capture.error_detail = detail[:300]
    capture.processing_started_at = None
    capture.save(
        update_fields=[
            "recognition_attempts",
            "needs_new_attempt",
            "retryable",
            "response_status",
            "error_code",
            "error_detail",
            "processing_started_at",
            "updated_at",
        ]
    )
    return capture


def _capture_new_episode(capture_id: int) -> AutomaticPassageCapture:
    try:
        with scale.authoritative_capture(scale.TRUCK_SCALE_KEY):
            read_started_at = timezone.now()
            reading = scale.read_truck_scale(scale.TRUCK_SCALE_KEY)
            stable_weight_at = read_started_at - timedelta(
                seconds=float(reading.age_seconds)
            )
            _persist_scale_sample(
                capture_id,
                reading,
                stable_weight_at=stable_weight_at,
            )
            return _recognize_capture(capture_id, retry_only=False)
    except _CaptureRejected as error:
        # No sample was stored: the lane just needs a fresh clear edge.
        return _finish_error(
            capture_id,
            status_code=error.status_code,
            code=error.code,
            detail=error.detail,
            retryable=False,
            requires_acknowledgement=False,
        )
    except APIException as error:
        detail, code = _api_exception_parts(error)
        return _finish_error(
            capture_id,
            status_code=int(error.status_code),
            code=code,
            detail=detail,
            retryable=False,
            requires_acknowledgement=False,
        )


def _attach_capture_photo(capture: AutomaticPassageCapture) -> None:
    """Best-effort evidence photo after the weight is already committed."""

    if capture.status != AutomaticPassageCapture.COMPLETED:
        return
    try:
        attach_photo(capture.camera, _attempt_request_id(capture))
    except Exception:  # noqa: BLE001 - a photo must never break the loop
        log.exception("Automatic passage photo failed capture_id=%s", capture.pk)


def _run_work(work: _Work) -> AutomaticPassageCapture:
    if work.kind == "capture":
        capture = _capture_new_episode(work.capture_id)
    elif work.kind == "recognize_retry":
        capture = _recognize_capture(work.capture_id, retry_only=True)
    elif work.kind == "recognize_again":
        capture = _recognize_again(work.capture_id)
    elif work.kind == "apply":
        capture = _apply_recognized_capture(work.capture_id)
    else:
        raise RuntimeError(f"Unknown passage scale work: {work.kind}")
    _attach_capture_photo(capture)
    return capture


def _active_runtime_payload(
    capture: AutomaticPassageCapture | None,
) -> dict | None:
    if capture is None:
        return None
    return {
        "request_id": str(capture.idempotency_key),
        "stage": capture.stage,
        "action": capture.action or None,
        "wagon_id": capture.wagon_id,
        "retryable": bool(capture.retryable),
        "error_code": capture.error_code or None,
    }


def _public_state(
    state: PassageScaleAutomationState | None,
    capture: AutomaticPassageCapture | None,
    *,
    unavailable: bool,
) -> str:
    if capture is not None and capture.needs_operator:
        return "manual_required"
    if state is not None and state.phase == PassageScaleAutomationState.PROCESSING:
        if capture is not None and capture.stage == AutomaticPassageCapture.RECOGNIZING:
            return "recognizing"
        if capture is not None and capture.stage == AutomaticPassageCapture.APPLYING:
            return "applying"
        return "candidate"
    if unavailable:
        return "unavailable"
    if state is None or state.phase in {
        PassageScaleAutomationState.UNARMED,
        PassageScaleAutomationState.STABILIZING,
    }:
        return "candidate"
    if state.phase == PassageScaleAutomationState.ARMED:
        return "idle"
    if state.phase == PassageScaleAutomationState.AWAITING_CLEAR:
        return "awaiting_clear"
    return "unavailable"


def _store_runtime(payload: dict) -> None:
    """Best-effort runtime projection without flooding logs every poll."""

    global _runtime_cache_write_failed
    try:
        cache.set(
            RUNTIME_CACHE_KEY,
            payload,
            timeout=settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS * 2,
        )
    except Exception:
        if not _runtime_cache_write_failed:
            log.exception("Could not publish automatic passage scale runtime")
        _runtime_cache_write_failed = True
    else:
        if _runtime_cache_write_failed:
            log.info("Automatic passage scale runtime publishing recovered")
        _runtime_cache_write_failed = False


def _load_runtime() -> object:
    """Best-effort runtime lookup with transition-only failure logging."""

    global _runtime_cache_read_failed
    try:
        payload = cache.get(RUNTIME_CACHE_KEY)
    except Exception:
        if not _runtime_cache_read_failed:
            log.exception("Could not read automatic passage scale runtime")
        _runtime_cache_read_failed = True
        return None
    if _runtime_cache_read_failed:
        log.info("Automatic passage scale runtime reads recovered")
    _runtime_cache_read_failed = False
    return payload


def _publish_runtime(*, now, unavailable: bool = False) -> dict:
    state = (
        PassageScaleAutomationState.objects.select_related("current_capture")
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    capture = state.current_capture if state is not None else None
    payload = {
        "enabled": True,
        "stable_weight_seconds": _stable_weight_seconds(state),
        "state": _public_state(state, capture, unavailable=unavailable),
        "last_checked_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "heartbeat_stale": False,
        "active": _active_runtime_payload(capture),
    }
    _store_runtime(payload)
    return payload


def _durable_lane() -> tuple[
    PassageScaleAutomationState | None,
    AutomaticPassageCapture | None,
]:
    state = (
        PassageScaleAutomationState.objects.select_related("current_capture")
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    capture = state.current_capture if state is not None else None
    return state, capture


def _stable_weight_seconds(
    state: PassageScaleAutomationState | None,
) -> int:
    if state is None:
        return PASSAGE_SCALE_DEFAULT_STABLE_WEIGHT_SECONDS
    return int(state.stable_weight_seconds)


def scale_automation_settings() -> dict:
    """Return the durable operator-editable timing for the truck lane."""

    state = (
        PassageScaleAutomationState.objects.filter(scale_number=scale.TRUCK_SCALE_KEY)
        .only("stable_weight_seconds")
        .first()
    )
    return {"stable_weight_seconds": _stable_weight_seconds(state)}


@transaction.atomic
def update_scale_automation_settings(
    *,
    stable_weight_seconds: int,
    user,
) -> dict:
    """Update lane timing without letting an in-flight candidate fire early."""

    state, _created = (
        PassageScaleAutomationState.objects.select_for_update().get_or_create(
            scale_number=scale.TRUCK_SCALE_KEY,
            defaults={"phase": PassageScaleAutomationState.UNARMED},
        )
    )
    previous = int(state.stable_weight_seconds)
    changed = previous != stable_weight_seconds
    update_fields = ["stable_weight_seconds"] if changed else []
    if state.phase == PassageScaleAutomationState.STABILIZING:
        # A shorter value must not retroactively accept a partly observed
        # vehicle. The next occupied poll starts one complete new interval.
        _reset_candidate(state, phase=PassageScaleAutomationState.ARMED)
        update_fields.extend(
            [
                "phase",
                "stable_streak",
                "stability_started_at",
                "candidate_weight_kg",
            ]
        )
    state.stable_weight_seconds = stable_weight_seconds
    if update_fields:
        _save_state(state, *update_fields)
    if changed:
        log_event(
            "grain_auto_scale_settings_updated",
            "Изменено время подтверждения стабильного веса",
            user=user,
            payload={
                "scale_number": scale.TRUCK_SCALE_KEY,
                "previous_stable_weight_seconds": previous,
                "stable_weight_seconds": stable_weight_seconds,
            },
        )
    return {"stable_weight_seconds": stable_weight_seconds}


def _durable_runtime_fallback(*, last_checked_at: str | None = None) -> dict:
    """Project durable lane state when the best-effort heartbeat is unusable."""

    state, capture = _durable_lane()
    return {
        "enabled": True,
        "stable_weight_seconds": _stable_weight_seconds(state),
        "state": _public_state(state, capture, unavailable=True),
        "last_checked_at": last_checked_at,
        "heartbeat_stale": True,
        "active": _active_runtime_payload(capture),
    }


def acknowledge_failure(
    idempotency_key: UUID,
    *,
    user,
    now=None,
) -> dict:
    """Acknowledge one terminal failure and require a new physical clear edge."""

    current = now or timezone.now()
    with transaction.atomic():
        state, _created = (
            PassageScaleAutomationState.objects.select_for_update().get_or_create(
                scale_number=scale.TRUCK_SCALE_KEY,
                defaults={"phase": PassageScaleAutomationState.UNARMED},
            )
        )
        try:
            capture = AutomaticPassageCapture.objects.select_for_update().get(
                idempotency_key=idempotency_key
            )
        except AutomaticPassageCapture.DoesNotExist as exc:
            raise ValidationError(
                {
                    "detail": "Операция автоматических весов не найдена.",
                    "code": "automatic_scale_capture_not_found",
                }
            ) from exc
        if capture.status != AutomaticPassageCapture.FAILED:
            raise ValidationError(
                {
                    "detail": "Подтвердить можно только операцию, требующую ручной обработки.",
                    "code": "automatic_scale_capture_not_failed",
                }
            )
        if capture.acknowledged_at is None:
            if state.current_capture_id != capture.pk:
                raise ValidationError(
                    {
                        "detail": "Состояние автоматических весов уже изменилось.",
                        "code": "automatic_scale_state_changed",
                    }
                )
            capture.acknowledged_at = current
            capture.acknowledged_by = user
            capture.save(
                update_fields=[
                    "acknowledged_at",
                    "acknowledged_by",
                    "updated_at",
                ]
            )
            log_event(
                "grain_automatic_scale_acknowledged",
                "Ручная обработка сбоя автоматических весов подтверждена",
                user=user,
                payload={
                    "capture_id": capture.pk,
                    "request_id": str(capture.idempotency_key),
                    "error_code": capture.error_code,
                },
            )
            log.warning(
                "Automatic passage scale failure acknowledged capture_id=%s user_id=%s",
                capture.pk,
                getattr(user, "pk", None),
            )
        if state.current_capture_id == capture.pk:
            # ``cleared_at`` belongs to the failed episode and may predate a
            # manual fallback now occupying the same scale.  Never reuse it to
            # arm automation: post-ack observations must prove a fresh clear.
            _disarm_lane(state)

    if settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        return _publish_runtime(now=current)
    return scale_automation_runtime(now=current)


def scale_automation_runtime(*, now=None) -> dict:
    """Return a permission-safe, fail-closed UI projection."""

    if not settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        durable_state, durable_capture = _durable_lane()
        if (
            _public_state(
                durable_state,
                durable_capture,
                unavailable=False,
            )
            == "manual_required"
        ):
            # The kill switch stops new automation; it must not hide an
            # unresolved operation that still requires an explicit audit ack.
            return {
                "enabled": False,
                "stable_weight_seconds": _stable_weight_seconds(durable_state),
                "state": "manual_required",
                "last_checked_at": None,
                "heartbeat_stale": False,
                "active": _active_runtime_payload(durable_capture),
            }
        return {
            "enabled": False,
            "stable_weight_seconds": _stable_weight_seconds(durable_state),
            "state": "disabled",
            "last_checked_at": None,
            "heartbeat_stale": False,
            "active": None,
        }
    current = now or timezone.now()
    payload = _load_runtime()
    if (
        not isinstance(payload, dict)
        or payload.get("state") not in PUBLIC_RUNTIME_STATES
    ):
        return _durable_runtime_fallback()
    checked = parse_datetime(str(payload.get("last_checked_at") or ""))
    stale = (
        checked is None
        or timezone.is_naive(checked)
        or checked > current + timedelta(seconds=5)
        or checked
        < current
        - timedelta(seconds=settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS)
    )
    if stale:
        last_checked_at = payload.get("last_checked_at")
        return _durable_runtime_fallback(
            last_checked_at=(
                last_checked_at if isinstance(last_checked_at, str) else None
            )
        )
    durable_state, durable_capture = _durable_lane()
    stable_weight_seconds = _stable_weight_seconds(durable_state)
    durable_public_state = _public_state(
        durable_state,
        durable_capture,
        unavailable=False,
    )
    if durable_public_state in {
        "candidate",
        "recognizing",
        "applying",
    } and (
        durable_state is not None
        and durable_state.phase == PassageScaleAutomationState.PROCESSING
    ):
        # Hardware/OCR work intentionally runs outside the claim transaction.
        # Its durable stage is authoritative while Redis may still contain the
        # previous idle poll, so expose progress immediately from PostgreSQL.
        return {
            "enabled": True,
            "stable_weight_seconds": stable_weight_seconds,
            "state": durable_public_state,
            "last_checked_at": payload.get("last_checked_at"),
            "heartbeat_stale": False,
            "active": _active_runtime_payload(durable_capture),
        }
    if durable_public_state == "manual_required":
        return {
            "enabled": True,
            "stable_weight_seconds": stable_weight_seconds,
            "state": "manual_required",
            "last_checked_at": payload.get("last_checked_at"),
            "heartbeat_stale": False,
            "active": _active_runtime_payload(durable_capture),
        }
    if (
        durable_state is not None
        and durable_state.phase == PassageScaleAutomationState.AWAITING_CLEAR
        and payload.get("state") != "unavailable"
    ):
        return {
            "enabled": True,
            "stable_weight_seconds": stable_weight_seconds,
            "state": "awaiting_clear",
            "last_checked_at": payload.get("last_checked_at"),
            "heartbeat_stale": False,
            "active": _active_runtime_payload(durable_capture),
        }
    return {
        "enabled": True,
        "stable_weight_seconds": stable_weight_seconds,
        "state": payload["state"],
        "last_checked_at": payload.get("last_checked_at"),
        "heartbeat_stale": False,
        "active": payload.get("active"),
    }


def monitor_once(*, now=None) -> MonitorIteration:
    """Execute one sequential poll and any claimed durable work."""

    current = now or timezone.now()
    if not settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        _prepare_disabled_lane(now=current)
        payload = {
            "enabled": False,
            "stable_weight_seconds": scale_automation_settings()[
                "stable_weight_seconds"
            ],
            "state": "disabled",
            "last_checked_at": current.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "heartbeat_stale": False,
            "active": None,
        }
        _store_runtime(payload)
        return MonitorIteration(state="disabled")

    pending, pending_work = _claim_pending_work(now=current)
    if pending:
        capture = _run_work(pending_work) if pending_work is not None else None
        payload = _publish_runtime(now=timezone.now())
        return MonitorIteration(
            state=str(payload["state"]),
            capture_id=capture.pk if capture is not None else None,
            action=capture.action if capture is not None else "",
            wagon_id=capture.wagon_id if capture is not None else None,
            error_code=capture.error_code if capture is not None else "",
        )

    try:
        observation = scale.read_truck_scale_observation(scale.TRUCK_SCALE_KEY)
    except APIException as error:
        _discard_unobserved_candidate()
        _publish_runtime(now=current, unavailable=True)
        code = error.get_codes()
        return MonitorIteration(
            state="unavailable",
            error_code=str(code)
            if isinstance(code, str)
            else "truck_scale_unavailable",
        )

    work = _advance_lane(observation, now=current)
    capture = _run_work(work) if work is not None else None

    dependency_unavailable = observation.state in {
        "disconnected",
        "unavailable",
        "stale",
        "malformed",
    }
    payload = _publish_runtime(
        now=timezone.now(),
        unavailable=dependency_unavailable,
    )
    return MonitorIteration(
        state=str(payload["state"]),
        capture_id=capture.pk if capture is not None else None,
        action=capture.action if capture is not None else "",
        wagon_id=capture.wagon_id if capture is not None else None,
        error_code=capture.error_code if capture is not None else "",
    )
