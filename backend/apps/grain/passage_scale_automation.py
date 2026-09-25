"""Automatic passage apply: collector weight + OCR -> trip status.

The independent weighbridge collector owns the scale edge detector and the
camera call; its outbox importer replays every saved weighing through
``_apply_recognized_capture``. This module also keeps the operator-editable
lane timing and the runtime projection the UI reads.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.cameras import ai as camera_ai
from apps.cameras.models import VehiclePlateEvent
from apps.common.datetimes import parse_aware_datetime
from apps.eventlog.services import log_event

from . import scale, services
from .models import (
    PASSAGE_SCALE_DEFAULT_STABLE_WEIGHT_SECONDS,
    AutomaticPassageCapture,
    PassageScaleAutomationState,
    UnassignedWeighing,
)
from .plate_recognition import (
    RECOGNITION_FIELDS,
    api_exception_parts,
    apply_recognition,
    recognized_at_for,
    safe_ai_payload,
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


class _CaptureRejected(Exception):
    def __init__(self, code: str, detail: str, *, status_code: int = 409) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _reset_candidate(state: PassageScaleAutomationState, *, phase: str) -> None:
    state.phase = phase
    state.stable_streak = 0
    state.stability_started_at = None
    state.candidate_weight_kg = None


def _save_state(state: PassageScaleAutomationState, *fields: str) -> None:
    state.save(update_fields=[*fields, "updated_at"])


def _mark_plate_unresolved(
    capture: AutomaticPassageCapture,
    *,
    now,
    code: str,
    detail: str,
) -> None:
    capture.plate_unresolved = True
    capture.stage = AutomaticPassageCapture.APPLYING
    capture.retryable = False
    capture.response_status = None
    capture.error_code = code[:64]
    capture.error_detail = detail[:300]
    capture.processing_started_at = now
    capture.save(
        update_fields=[
            "plate_unresolved",
            "stage",
            "retryable",
            "response_status",
            "error_code",
            "error_detail",
            "processing_started_at",
            "updated_at",
        ]
    )


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


def _attempt_request_id(capture: AutomaticPassageCapture) -> UUID:
    return capture.attempt_request_id or capture.idempotency_key


def park_capture(capture: AutomaticPassageCapture, reason: str) -> UnassignedWeighing:
    """Keep the capture's stable weight as an operator-resolvable weighing (once)."""

    item, _ = UnassignedWeighing.objects.get_or_create(
        capture=capture,
        defaults={
            "weight_kg": capture.weight_kg,
            "stable_weight_at": capture.stable_weight_at,
            "scale_number": capture.scale_number,
            "scale_age_seconds": capture.scale_age_seconds,
            "scale_updated_at": capture.scale_updated_at,
            "camera": capture.camera,
            "photo_request_id": _attempt_request_id(capture),
            "vehicle_number": capture.vehicle_number,
            "orientation": capture.orientation,
            "reason": reason,
        },
    )
    return item


def park_failed_capture(capture: AutomaticPassageCapture) -> UnassignedWeighing | None:
    """A failed capture with a stable weight goes to the operator's queue.

    The weight is resolved there instead of an acknowledge-only failure, so
    the capture no longer requires an acknowledgement. Callers hold its lock.
    """

    if (
        capture.status != AutomaticPassageCapture.FAILED
        or capture.weight_kg is None
        or capture.stable_weight_at is None
    ):
        return None
    item = park_capture(capture, capture.error_code or "automatic_passage_apply_failed")
    capture.requires_acknowledgement = False
    capture.save(update_fields=["requires_acknowledgement", "updated_at"])
    return item


def _attempt_stable_weight_at(capture: AutomaticPassageCapture):
    return capture.attempt_stable_weight_at or capture.stable_weight_at


def _store_orientation(capture: AutomaticPassageCapture, payload) -> None:
    """Keep the latest decisive front/rear verdict across OCR attempts."""

    label, _confidence = camera_ai.vehicle_orientation(payload)
    if label:
        capture.orientation = label


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

    recognized_at = recognized_at_for(payload, _attempt_stable_weight_at(capture))
    if recognized_at is None:
        raise _CaptureRejected(
            "vehicle_recognition_malformed",
            "Camera-PC вернул некорректные временные метки.",
            status_code=502,
        )

    confirmation = payload["confirmation"]
    safe_payload = safe_ai_payload(payload)
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

    apply_recognition(
        capture, payload, recognized_at=recognized_at, safe_payload=safe_payload
    )
    _store_orientation(capture, payload)
    capture.vehicle_plate_event = event
    capture.processing_started_at = timezone.now()
    capture.save(
        update_fields=[
            *RECOGNITION_FIELDS,
            "orientation",
            "vehicle_plate_event",
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
) -> AutomaticPassageCapture:
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if capture.status != AutomaticPassageCapture.PROCESSING:
        return capture

    capture.retryable = retryable
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
    if not retryable:
        capture.status = AutomaticPassageCapture.FAILED
        capture.stage = AutomaticPassageCapture.DONE
        capture.requires_acknowledgement = True
        capture.completed_at = timezone.now()
        update_fields.extend(
            ["status", "stage", "requires_acknowledgement", "completed_at"]
        )
    capture.save(update_fields=update_fields)
    return capture


@transaction.atomic
def _finish_success(
    capture_id: int,
    *,
    result: services.VehiclePlateAutomationResult,
) -> AutomaticPassageCapture:
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if capture.status != AutomaticPassageCapture.PROCESSING:
        return capture
    capture.status = AutomaticPassageCapture.COMPLETED
    capture.stage = AutomaticPassageCapture.DONE
    capture.action = result.action
    if result.unassigned_id is not None:
        UnassignedWeighing.objects.filter(
            pk=result.unassigned_id, capture__isnull=True
        ).update(capture=capture)
    capture.wagon_id = result.wagon_id
    capture.retryable = False
    capture.response_status = 200
    if (
        not capture.plate_unresolved
        and result.action != services.AUTO_ACTION_UNASSIGNED
    ):
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
    # operation and its terminal capture update so a manual mutation can never
    # interleave between "applied" and "completed".
    scale.configure_authoritative_db_timeouts()
    state = (
        PassageScaleAutomationState.objects.select_for_update()
        .filter(scale_number=scale.TRUCK_SCALE_KEY)
        .first()
    )
    capture = AutomaticPassageCapture.objects.select_for_update().get(pk=capture_id)
    if (
        state is None
        or capture.status != AutomaticPassageCapture.PROCESSING
        or capture.stage != AutomaticPassageCapture.APPLYING
    ):
        return capture
    try:
        reading = _reading_from_capture(capture)
        from .weighing_identity import defer_exit
        deferred = defer_exit(capture)
        if deferred is not None:
            return _finish_success(capture_id, result=deferred)
        if capture.plate_unresolved:
            result = services.apply_unidentified_passage_scale_sample(
                reading=reading,
                camera=capture.camera,
                request_id=_attempt_request_id(capture),
                stable_weight_at=capture.stable_weight_at,
                capture=capture,
                orientation=capture.orientation,
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
                orientation=capture.orientation,
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
        detail, code = api_exception_parts(error)
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
    if not result.retryable and capture.weight_kg is not None:
        # Preserve a business conflict as an operator-resolvable weighing.
        # The physical lane continues; its result is never guessed away.
        item = park_capture(capture, result.error or "passage_state_conflict")
        capture.error_code = item.reason
        capture.save(update_fields=["error_code", "updated_at"])
        return _finish_success(
            capture_id,
            result=services.VehiclePlateAutomationResult(
                status="processed",
                action=services.AUTO_ACTION_UNASSIGNED,
                weight_kg=capture.weight_kg,
                unassigned_id=item.pk,
            ),
        )
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

    state = services.lock_passage_lane()
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
    """Acknowledge one terminal failure and release the lane it latched."""

    current = now or timezone.now()
    with transaction.atomic():
        state = services.lock_passage_lane()
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
            services.reset_passage_lane(state)

    # The collector's importer owns the runtime projection; never overwrite it.
    return scale_automation_runtime(now=current)


def scale_automation_runtime(*, now=None) -> dict:
    """Return a permission-safe, fail-closed UI projection."""

    if not settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        durable_state, durable_capture = _durable_lane()
        # The kill switch stops new automation; it must not hide an
        # unresolved operation that still requires an explicit audit ack.
        manual_required = (
            _public_state(durable_state, durable_capture, unavailable=False)
            == "manual_required"
        )
        return {
            "enabled": False,
            "stable_weight_seconds": _stable_weight_seconds(durable_state),
            "state": "manual_required" if manual_required else "disabled",
            "last_checked_at": None,
            "heartbeat_stale": False,
            "active": (
                _active_runtime_payload(durable_capture) if manual_required else None
            ),
        }
    current = now or timezone.now()
    payload = _load_runtime()
    if (
        not isinstance(payload, dict)
        or payload.get("state") not in PUBLIC_RUNTIME_STATES
    ):
        return _durable_runtime_fallback()
    checked = parse_aware_datetime(payload.get("last_checked_at"))
    stale = (
        checked is None
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
    # Only the collector's importer publishes the runtime; the lane timing is
    # read from PostgreSQL so a settings change shows before the next poll.
    return {
        **payload,
        "stable_weight_seconds": scale_automation_settings()["stable_weight_seconds"],
        "heartbeat_stale": False,
    }
