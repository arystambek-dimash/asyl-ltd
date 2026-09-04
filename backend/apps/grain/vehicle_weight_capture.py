"""Weight-first passage coordinator: stable scale -> camera OCR -> atomic apply."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, timedelta
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, InterfaceError, OperationalError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException

from apps.cameras import ai as camera_ai

from . import scale
from .models import PassageWeightCapture, Wagon
from .weighing_photos import attach_photo

KZ_VEHICLE_PLATE_RE = re.compile(r"^[0-9]{3}[A-Z]{2,3}[0-9]{2}$")


class PassageCaptureError(APIException):
    """Stable API error carrying the durable capture state back to the UI."""

    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        code: str,
        request_id: UUID | str,
        recognition_status: str,
        retryable: bool,
    ) -> None:
        self.status_code = status_code
        super().__init__(detail, code=code)
        # APIException normally coerces every leaf to ErrorDetail, including a
        # boolean. Keep these response-contract fields strongly typed.
        self.detail = {
            "detail": detail,
            "code": code,
            "request_id": str(request_id),
            "recognition_status": recognition_status,
            "retryable": retryable,
        }


class _ApplyRejected(Exception):
    def __init__(self, detail: str, code: str) -> None:
        self.detail = detail
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class _CaptureClaim:
    capture_id: int
    state: str


def _grain_services():
    # Lazy import keeps the coordinator separate without creating a module cycle.
    from . import services

    return services


def _processing_lease() -> timedelta:
    timeout = float(settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS)
    # Keep an in-flight owner well beyond the nominal HTTP timeout. The scale
    # subsystem uses the same 90-second minimum for its physical-operation
    # lease, while detector/OCR cleanup is not a strict socket deadline.
    return timedelta(seconds=max(90.0, timeout * 3.0 + 15.0))


def _capture_exception(capture: PassageWeightCapture) -> PassageCaptureError:
    status_code = int(capture.response_status or (503 if capture.retryable else 409))
    return PassageCaptureError(
        status_code=status_code,
        detail=capture.error_detail or "Не удалось распознать номер машины.",
        code=capture.error_code or "vehicle_recognition_failed",
        request_id=capture.idempotency_key,
        recognition_status=(
            capture.stage
            if capture.status == PassageWeightCapture.PROCESSING
            else capture.status
        ),
        retryable=bool(capture.retryable),
    )


def _completed_wagon_or_raise(capture: PassageWeightCapture) -> Wagon:
    """Turn a completion/error race into the already-committed success."""

    if capture.status == PassageWeightCapture.COMPLETED and capture.wagon_id:
        wagon = Wagon.objects.filter(pk=capture.wagon_id).first()
        if wagon is not None:
            return wagon
    if capture.status == PassageWeightCapture.COMPLETED:
        raise PassageCaptureError(
            status_code=409,
            detail="Операция завершена, но рейс уже удалён.",
            code="passage_capture_wagon_missing",
            request_id=capture.idempotency_key,
            recognition_status=PassageWeightCapture.COMPLETED,
            retryable=False,
        )
    raise _capture_exception(capture)


def _transient_exception(
    capture: PassageWeightCapture,
    *,
    status_code: int,
    detail: str,
    code: str,
) -> PassageCaptureError:
    return PassageCaptureError(
        status_code=status_code,
        detail=detail,
        code=code,
        request_id=capture.idempotency_key,
        recognition_status=capture.stage,
        retryable=True,
    )


def _api_exception_parts(error: APIException) -> tuple[str, str]:
    details = error.detail
    codes = error.get_codes()
    if isinstance(details, dict):
        raw_detail = details.get("detail")
        detail = str(raw_detail) if raw_detail else str(error)
    else:
        detail = str(details)
    if isinstance(details, dict) and details.get("code"):
        code = str(details["code"])
    elif isinstance(codes, dict):
        raw_code = codes.get("code") or codes.get("detail")
        code = str(raw_code) if raw_code else "passage_capture_rejected"
    else:
        code = str(codes or "passage_capture_rejected")
    return detail[:300], code[:64]


def _idempotency_conflict(idempotency_key: UUID) -> PassageCaptureError:
    return PassageCaptureError(
        status_code=409,
        detail="Этот Idempotency-Key уже использован для другой операции.",
        code="passage_capture_idempotency_conflict",
        request_id=idempotency_key,
        recognition_status="conflict",
        retryable=False,
    )


def _terminalize_interrupted_claim(
    capture: PassageWeightCapture,
    *,
    now,
) -> None:
    capture.status = PassageWeightCapture.FAILED
    capture.stage = PassageWeightCapture.DONE
    capture.retryable = False
    capture.response_status = 409
    capture.error_code = "passage_capture_interrupted"
    capture.error_detail = (
        "Предыдущее чтение весов прервалось до фиксации результата. "
        "Создайте новую попытку."
    )
    capture.completed_at = now
    capture.save(
        update_fields=[
            "status",
            "stage",
            "retryable",
            "response_status",
            "error_code",
            "error_detail",
            "completed_at",
            "updated_at",
        ]
    )


def _classify_existing_capture(
    capture: PassageWeightCapture,
    *,
    wagon_id: int,
    action: str,
    idempotency_key: UUID,
    now,
) -> _CaptureClaim:
    if capture.wagon_id_snapshot != wagon_id or capture.action != action:
        raise _idempotency_conflict(idempotency_key)
    if capture.status == PassageWeightCapture.COMPLETED:
        return _CaptureClaim(capture.pk, "completed")
    if capture.status == PassageWeightCapture.FAILED:
        return _CaptureClaim(capture.pk, "terminal")

    has_sample = capture.weight_kg is not None and capture.stable_weight_at is not None
    can_resume = has_sample and capture.stage in {
        PassageWeightCapture.RECOGNIZING,
        PassageWeightCapture.APPLYING,
    }
    # ``retryable`` is written only after the previous handler has stopped.
    # Clearing it under the Wagon lock is therefore an attempt-claim CAS: one
    # immediate retry proceeds while concurrent duplicates remain in-progress.
    if capture.retryable and can_resume:
        capture.retryable = False
        capture.error_code = ""
        capture.error_detail = ""
        capture.response_status = None
        capture.save(
            update_fields=[
                "retryable",
                "error_code",
                "error_detail",
                "response_status",
                "updated_at",
            ]
        )
        return _CaptureClaim(capture.pk, "resume")

    if capture.updated_at >= now - _processing_lease():
        raise _transient_exception(
            capture,
            status_code=409,
            detail="Распознавание номера и фиксация веса уже выполняются.",
            code="passage_capture_in_progress",
        )
    if not can_resume:
        _terminalize_interrupted_claim(capture, now=now)
        return _CaptureClaim(capture.pk, "terminal")

    capture.retryable = False
    capture.error_code = ""
    capture.error_detail = ""
    capture.response_status = None
    capture.save(
        update_fields=[
            "retryable",
            "error_code",
            "error_detail",
            "response_status",
            "updated_at",
        ]
    )
    return _CaptureClaim(capture.pk, "resume")


@transaction.atomic
def _begin_capture(
    *,
    wagon_id: int,
    action: str,
    user,
    idempotency_key: UUID,
    now,
) -> _CaptureClaim:
    grain_services = _grain_services()
    # When automatic polling is enabled, every new manual physical capture
    # follows State -> automatic capture -> Wagon -> PassageWeightCapture.
    # Existing idempotency keys remain replayable even while another automatic
    # episode is active because they never read the scale a second time.
    automation_state, automation_capture = (
        grain_services._lock_automatic_passage_lane()
    )
    wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon_id)
    existing = (
        PassageWeightCapture.objects.select_for_update()
        .filter(idempotency_key=idempotency_key)
        .first()
    )
    if existing is not None:
        return _classify_existing_capture(
            existing,
            wagon_id=wagon_id,
            action=action,
            idempotency_key=idempotency_key,
            now=now,
        )

    grain_services._assert_automatic_passage_lane_allows_manual_operation(
        automation_state,
        automation_capture,
    )

    if not wagon.is_passage:
        raise PassageCaptureError(
            status_code=400,
            detail="Распознавание номера по весу доступно только для вывоза.",
            code="not_passage",
            request_id=idempotency_key,
            recognition_status="rejected",
            retryable=False,
        )
    grain_services._ensure_scale_action_ready(wagon, action)
    active = (
        PassageWeightCapture.objects.select_for_update()
        .filter(
            wagon=wagon,
            action=action,
            status__in=[
                PassageWeightCapture.PROCESSING,
                PassageWeightCapture.COMPLETED,
            ],
        )
        .first()
    )
    if active is not None:
        if (
            active.status == PassageWeightCapture.PROCESSING
            and active.updated_at < now - _processing_lease()
            and (
                active.stage == PassageWeightCapture.CLAIMED
                or active.weight_kg is None
                or active.stable_weight_at is None
            )
        ):
            _terminalize_interrupted_claim(active, now=now)
            active = None
    if active is not None:
        resumable = active.status == PassageWeightCapture.PROCESSING
        raise PassageCaptureError(
            status_code=409,
            detail=(
                "Для этого этапа уже выполняется операция. "
                "Повторите запрос с её исходным идентификатором."
                if resumable
                else "Для этого этапа рейса уже существует другая операция."
            ),
            code=(
                "passage_capture_resume_required"
                if resumable
                else "passage_capture_busy"
            ),
            request_id=active.idempotency_key,
            recognition_status=(active.stage if resumable else active.status),
            retryable=resumable,
        )
    try:
        # A savepoint keeps the outer transaction usable if the globally
        # unique UUID concurrently wins on another Wagon row.
        with transaction.atomic():
            capture = PassageWeightCapture.objects.create(
                idempotency_key=idempotency_key,
                wagon=wagon,
                wagon_id_snapshot=wagon.pk,
                action=action,
                wagon_status_before=wagon.status,
                camera=settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA,
                requested_by=user,
            )
    except IntegrityError:
        winner = (
            PassageWeightCapture.objects.select_for_update()
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if winner is None:
            raise
        return _classify_existing_capture(
            winner,
            wagon_id=wagon_id,
            action=action,
            idempotency_key=idempotency_key,
            now=now,
        )
    grain_services._fence_automatic_passage_lane_for_manual_mutation(
        automation_state
    )
    return _CaptureClaim(capture.pk, "new")


def _lock_wagon_then_capture(
    capture_id: int,
) -> tuple[Wagon, PassageWeightCapture]:
    hint = (
        PassageWeightCapture.objects.only("wagon_id")
        .filter(pk=capture_id)
        .first()
    )
    if hint is None or hint.wagon_id is None:
        raise _ApplyRejected(
            "Рейс операции больше не существует.",
            "passage_capture_wagon_missing",
        )
    wagon = (
        Wagon.objects.select_for_update(of=("self",))
        .filter(pk=hint.wagon_id)
        .first()
    )
    if wagon is None:
        raise _ApplyRejected(
            "Рейс операции больше не существует.",
            "passage_capture_wagon_missing",
        )
    capture = PassageWeightCapture.objects.select_for_update().get(pk=capture_id)
    if capture.wagon_id != wagon.pk:
        raise _ApplyRejected(
            "Связь операции с рейсом изменилась.",
            "passage_capture_wagon_changed",
        )
    return wagon, capture


@transaction.atomic
def _persist_scale_reading(
    capture_id: int,
    reading: scale.ScaleReading,
    *,
    stable_weight_at,
) -> PassageWeightCapture:
    wagon, capture = _lock_wagon_then_capture(capture_id)
    if (
        capture.status != PassageWeightCapture.PROCESSING
        or capture.stage != PassageWeightCapture.CLAIMED
    ):
        raise _ApplyRejected(
            "Состояние операции взвешивания изменилось.",
            "passage_capture_state_changed",
        )
    if wagon.status != capture.wagon_status_before:
        raise _ApplyRejected(
            "Состояние рейса изменилось во время чтения весов.",
            "wagon_changed_during_scale_read",
    )
    _grain_services()._ensure_scale_action_ready(wagon, capture.action)
    weight_kg = _grain_services()._whole_scale_weight_kg(reading)
    capture.weight_kg = weight_kg
    capture.scale_number = scale.TRUCK_SCALE_KEY
    capture.scale_age_seconds = reading.age_seconds
    capture.scale_updated_at = reading.updated_at or ""
    capture.stable_weight_at = stable_weight_at
    capture.stage = PassageWeightCapture.RECOGNIZING
    capture.retryable = False
    capture.error_code = ""
    capture.error_detail = ""
    capture.response_status = None
    capture.save(
        update_fields=[
            "weight_kg",
            "scale_number",
            "scale_age_seconds",
            "scale_updated_at",
            "stable_weight_at",
            "stage",
            "retryable",
            "error_code",
            "error_detail",
            "response_status",
            "updated_at",
        ]
    )
    return capture


def _safe_ai_payload(payload: Mapping | None) -> dict:
    """Keep bounded diagnostics, never an arbitrary upstream JSON document."""

    if not isinstance(payload, Mapping):
        return {}
    safe: dict[str, object] = {}
    string_limits = {
        "status": 64,
        "request_id": 36,
        "camera": 32,
        "source": 4,
        "stable_weight_at": 48,
        "recognized_at": 48,
        "vehicle_number": 30,
        "error": 300,
        "error_code": 64,
        "camera_status": 64,
        "active_input": 64,
        "roi_updated_at": 64,
    }
    for field, limit in string_limits.items():
        value = payload.get(field)
        if isinstance(value, str):
            safe[field] = value[:limit]
    for field in ("ok", "retryable"):
        value = payload.get(field)
        if isinstance(value, bool):
            safe[field] = value
    for field in (
        "fresh_frames_seen",
        "frames_scanned",
        "ambiguous_frames",
    ):
        value = payload.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe[field] = min(value, 1_000_000)

    confirmation = payload.get("confirmation")
    if isinstance(confirmation, Mapping):
        safe_confirmation: dict[str, int | float] = {}
        votes = confirmation.get("votes")
        if (
            isinstance(votes, int)
            and not isinstance(votes, bool)
            and 1 <= votes <= camera_ai.MAX_VEHICLE_CONFIRMATION_VOTES
        ):
            safe_confirmation["votes"] = votes
        for field in ("detector_confidence", "ocr_confidence"):
            value = confirmation.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                if math.isfinite(number) and 0 <= number <= 1:
                    safe_confirmation[field] = number
        if safe_confirmation:
            safe["confirmation"] = safe_confirmation
    return safe


@transaction.atomic
def _finish_capture_error(
    capture_id: int,
    *,
    status_code: int,
    code: str,
    detail: str,
    retryable: bool,
    ai_payload: Mapping | None = None,
) -> PassageWeightCapture:
    capture = PassageWeightCapture.objects.select_for_update().get(pk=capture_id)
    if capture.status != PassageWeightCapture.PROCESSING:
        return capture
    capture.retryable = retryable
    capture.response_status = status_code
    capture.error_code = code[:64]
    capture.error_detail = detail[:300]
    update_fields = [
        "retryable",
        "response_status",
        "error_code",
        "error_detail",
        "updated_at",
    ]
    if ai_payload is not None:
        capture.ai_payload_json = _safe_ai_payload(ai_payload)
        update_fields.append("ai_payload_json")
    if not retryable:
        capture.status = PassageWeightCapture.FAILED
        capture.stage = PassageWeightCapture.DONE
        capture.completed_at = timezone.now()
        update_fields.extend(["status", "stage", "completed_at"])
    capture.save(update_fields=update_fields)
    return capture


def _terminal_ai_error(error: camera_ai.AiError) -> tuple[int, str, str, bool]:
    payload = error.payload
    remote_status = str(payload.get("status") or "")
    if error.status in {401, 403}:
        return (
            502,
            "vehicle_recognition_auth_failed",
            "Camera-PC отклонил служебный ключ. Обратитесь к администратору.",
            False,
        )
    retryable_hint = payload.get("retryable")
    # A reverse proxy may return an HTML/empty 5xx response before the CV
    # service can attach its retryability contract. Preserve the stored scale
    # sample in that case: only an explicit ``retryable: false`` may make a
    # remote 5xx terminal.
    retryable = (
        retryable_hint is True
        or error.status == 202
        or (error.status >= 500 and retryable_hint is not False)
    )
    codes = {
        "processing": "vehicle_recognition_pending",
        "model_unavailable": "vehicle_model_unavailable",
        "on_demand_unavailable": "vehicle_recognition_not_configured",
        "no_match": "vehicle_plate_not_confirmed",
        "camera_unavailable": "vehicle_camera_unavailable",
        "roi_unavailable": "vehicle_roi_unavailable",
        "stale_weight_trigger": "stale_weight_trigger",
        "camera_not_configured": "vehicle_camera_not_configured",
        "idempotency_conflict": "vehicle_recognition_idempotency_conflict",
        "lane_busy": "vehicle_recognition_lane_busy",
        "capture_window_missed": "vehicle_capture_window_missed",
        "failed": "vehicle_recognition_failed",
        "interrupted": "vehicle_recognition_interrupted",
    }
    code = codes.get(remote_status, "vehicle_recognition_failed")
    status_code = 409 if error.status == 202 else int(error.status)
    return status_code, code, error.detail, retryable


def _canonical_timestamp(value) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@transaction.atomic
def _persist_ai_success(capture_id: int, payload: dict) -> PassageWeightCapture:
    capture = PassageWeightCapture.objects.select_for_update().get(pk=capture_id)
    if capture.status == PassageWeightCapture.COMPLETED:
        return capture
    if capture.status != PassageWeightCapture.PROCESSING:
        raise _capture_exception(capture)

    recognized_at = parse_datetime(str(payload.get("recognized_at") or ""))
    response_trigger = parse_datetime(str(payload.get("stable_weight_at") or ""))
    confirmation = payload.get("confirmation") or {}
    if (
        recognized_at is None
        or timezone.is_naive(recognized_at)
        or response_trigger is None
        or timezone.is_naive(response_trigger)
        or capture.stable_weight_at is None
        or response_trigger != capture.stable_weight_at
    ):
        raise _ApplyRejected(
            "AI-сервис вернул некорректные временные метки.",
            "vehicle_recognition_malformed",
        )

    capture.vehicle_number = str(payload["vehicle_number"])
    capture.camera_source = str(payload["source"])
    capture.recognized_at = recognized_at
    capture.confirmation_votes = int(confirmation["votes"])
    capture.detector_confidence = Decimal(str(confirmation["detector_confidence"]))
    capture.ocr_confidence = Decimal(str(confirmation["ocr_confidence"]))
    capture.ai_payload_json = _safe_ai_payload(payload)
    capture.response_status = 200
    capture.retryable = False
    capture.error_code = ""
    capture.error_detail = ""
    capture.stage = PassageWeightCapture.APPLYING
    capture.save(
        update_fields=[
            "vehicle_number",
            "camera_source",
            "recognized_at",
            "confirmation_votes",
            "detector_confidence",
            "ocr_confidence",
            "ai_payload_json",
            "response_status",
            "retryable",
            "error_code",
            "error_detail",
            "stage",
            "updated_at",
        ]
    )
    return capture


@transaction.atomic
def _apply_capture(capture_id: int, user) -> Wagon:
    scale.configure_authoritative_db_timeouts()
    wagon, capture = _lock_wagon_then_capture(capture_id)
    if capture.status == PassageWeightCapture.COMPLETED:
        return wagon
    if capture.status != PassageWeightCapture.PROCESSING:
        raise _capture_exception(capture)
    if capture.stage != PassageWeightCapture.APPLYING:
        raise _ApplyRejected(
            "Результат распознавания ещё не готов к сохранению.",
            "vehicle_recognition_not_ready",
        )
    if wagon.status != capture.wagon_status_before:
        raise _ApplyRejected(
            "Состояние рейса изменилось до сохранения веса.",
            "wagon_changed_during_vehicle_recognition",
        )

    services = _grain_services()
    services._ensure_scale_action_ready(wagon, capture.action)
    number = services.normalize_passage_number(capture.vehicle_number)
    if KZ_VEHICLE_PLATE_RE.fullmatch(number) is None:
        raise _ApplyRejected(
            "Камера не подтвердила корректный номер Казахстана.",
            "vehicle_plate_invalid",
        )
    current_number = services.normalize_passage_number(wagon.number)
    if current_number and current_number != number:
        raise _ApplyRejected(
            f"Камера распознала {number}, но в рейсе указан {current_number}.",
            "vehicle_plate_mismatch",
        )
    if capture.action == PassageWeightCapture.EXIT and not current_number:
        raise _ApplyRejected(
            "В рейсе нет номера, с которым можно сверить выезд.",
            "vehicle_plate_missing_on_passage",
        )

    if capture.action == PassageWeightCapture.ENTRY:
        wagon.number = number
        wagon.number_source = "camera"
        wagon.number_camera_source = capture.camera
        wagon.save(
            update_fields=["number", "number_source", "number_camera_source"]
        )

    kwargs = {
        "occurred_at": capture.stable_weight_at,
        "source": "scale",
        "scale_number": capture.scale_number,
        "scale_age_seconds": capture.scale_age_seconds,
        "scale_updated_at": capture.scale_updated_at or None,
        "photo_request_id": capture.idempotency_key,
        "photo_camera": capture.camera,
    }
    if capture.action == PassageWeightCapture.ENTRY:
        services.record_passage_entry_weight(wagon, capture.weight_kg, user, **kwargs)
    else:
        services.record_passage_exit_weight(wagon, capture.weight_kg, user, **kwargs)

    capture.status = PassageWeightCapture.COMPLETED
    capture.stage = PassageWeightCapture.DONE
    capture.retryable = False
    capture.response_status = 200
    capture.error_code = ""
    capture.error_detail = ""
    capture.completed_at = timezone.now()
    capture.save(
        update_fields=[
            "status",
            "stage",
            "retryable",
            "response_status",
            "error_code",
            "error_detail",
            "completed_at",
            "updated_at",
        ]
    )
    return wagon


def _recognize_and_apply(
    capture_id: int,
    user,
    *,
    retry_only: bool = False,
) -> Wagon:
    capture = PassageWeightCapture.objects.get(pk=capture_id)
    if capture.stage != PassageWeightCapture.APPLYING:
        try:
            recognize = (
                camera_ai.retry_vehicle_recognition_from_camera
                if retry_only
                else camera_ai.recognize_vehicle_from_camera
            )
            payload = recognize(
                capture.camera,
                capture.idempotency_key,
                _canonical_timestamp(capture.stable_weight_at),
            )
        except camera_ai.AiProtocolError:
            failed = _finish_capture_error(
                capture_id,
                status_code=502,
                code="vehicle_recognition_malformed",
                detail=(
                    "Camera-PC вернул некорректный результат распознавания. "
                    "Операция остановлена без повторного чтения весов."
                ),
                retryable=False,
            )
            return _completed_wagon_or_raise(failed)
        except camera_ai.AiUnavailable:
            failed = _finish_capture_error(
                capture_id,
                status_code=503,
                code="vehicle_recognition_unavailable",
                detail=(
                    "Связь с камерой прервалась. Повтор проверит только уже "
                    "созданный запрос и не прочитает весы второй раз."
                ),
                retryable=True,
            )
            return _completed_wagon_or_raise(failed)
        except camera_ai.AiError as error:
            status_code, code, detail, retryable = _terminal_ai_error(error)
            failed = _finish_capture_error(
                capture_id,
                status_code=status_code,
                code=code,
                detail=detail,
                retryable=retryable,
                ai_payload=error.payload,
            )
            return _completed_wagon_or_raise(failed)
        try:
            capture = _persist_ai_success(capture_id, payload)
        except _ApplyRejected as error:
            failed = _finish_capture_error(
                capture_id,
                status_code=502,
                code=error.code,
                detail=error.detail,
                retryable=False,
            )
            return _completed_wagon_or_raise(failed)

    try:
        wagon = _apply_capture(capture_id, user)
    except _ApplyRejected as error:
        failed = _finish_capture_error(
            capture_id,
            status_code=409,
            code=error.code,
            detail=error.detail,
            retryable=False,
        )
        return _completed_wagon_or_raise(failed)
    except IntegrityError:
        failed = _finish_capture_error(
            capture_id,
            status_code=409,
            code="vehicle_plate_in_use",
            detail="Этот номер уже привязан к другой машине на территории.",
            retryable=False,
        )
        return _completed_wagon_or_raise(failed)
    except APIException as error:
        detail, code = _api_exception_parts(error)
        retryable = int(error.status_code) >= 500
        failed = _finish_capture_error(
            capture_id,
            status_code=int(error.status_code),
            code=code,
            detail=detail,
            retryable=retryable,
        )
        return _completed_wagon_or_raise(failed)
    except (OperationalError, InterfaceError):
        failed = _finish_capture_error(
            capture_id,
            status_code=503,
            code="passage_capture_apply_unavailable",
            detail="Результат получен, но база временно не подтвердила сохранение.",
            retryable=True,
        )
        return _completed_wagon_or_raise(failed)
    # The weight is committed; the evidence photo is best effort.
    attach_photo(capture.camera, capture.idempotency_key)
    return wagon


def capture_passage_weight_and_plate(
    wagon: Wagon,
    action: str,
    user,
    *,
    idempotency_key: UUID,
) -> Wagon:
    """Perform or safely replay one weight-triggered passage recognition."""

    if not settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED:
        raise PassageCaptureError(
            status_code=503,
            detail="Распознавание номера после веса выключено.",
            code="vehicle_weight_first_disabled",
            request_id=idempotency_key,
            recognition_status="disabled",
            retryable=False,
        )
    if action not in {PassageWeightCapture.ENTRY, PassageWeightCapture.EXIT}:
        raise ValueError(f"Unknown passage capture action: {action}")

    claim = _begin_capture(
        wagon_id=wagon.pk,
        action=action,
        user=user,
        idempotency_key=idempotency_key,
        now=timezone.now(),
    )
    capture = PassageWeightCapture.objects.get(pk=claim.capture_id)
    if claim.state == "completed":
        return _completed_wagon_or_raise(capture)
    if claim.state == "terminal":
        raise _capture_exception(capture)
    if claim.state == "resume":
        return _recognize_and_apply(capture.pk, user, retry_only=True)

    try:
        with scale.authoritative_capture(scale.TRUCK_SCALE_KEY):
            try:
                # Capture the physical observation boundary before network I/O.
                # Any latency makes the trigger conservatively older, never
                # artificially newer after waiting for a database row lock.
                read_started_at = timezone.now()
                reading = scale.read_truck_scale(scale.TRUCK_SCALE_KEY)
                stable_weight_at = read_started_at - timedelta(
                    seconds=float(reading.age_seconds)
                )
                _persist_scale_reading(
                    capture.pk,
                    reading,
                    stable_weight_at=stable_weight_at,
                )
            except _ApplyRejected as error:
                failed = _finish_capture_error(
                    capture.pk,
                    status_code=409,
                    code=error.code,
                    detail=error.detail,
                    retryable=False,
                )
                return _completed_wagon_or_raise(failed)
            except APIException as error:
                detail, code = _api_exception_parts(error)
                failed = _finish_capture_error(
                    capture.pk,
                    status_code=int(error.status_code),
                    code=code,
                    detail=detail,
                    retryable=False,
                )
                return _completed_wagon_or_raise(failed)
            except (OperationalError, InterfaceError):
                failed = _finish_capture_error(
                    capture.pk,
                    status_code=503,
                    code="passage_capture_apply_unavailable",
                    detail="Не удалось безопасно сохранить показание весов.",
                    retryable=False,
                )
                return _completed_wagon_or_raise(failed)
            return _recognize_and_apply(capture.pk, user)
    except PassageCaptureError:
        raise
    except scale.TruckScaleCaptureBusy:
        # The durable claim did not sample hardware. Keep it terminal so the
        # same UUID can never accidentally capture a later vehicle.
        failed = _finish_capture_error(
            capture.pk,
            status_code=409,
            code="truck_scale_capture_busy",
            detail="Весы уже фиксируют другое взвешивание.",
            retryable=False,
        )
        return _completed_wagon_or_raise(failed)
    except APIException as error:
        detail, code = _api_exception_parts(error)
        failed = _finish_capture_error(
            capture.pk,
            status_code=int(error.status_code),
            code=code,
            detail=detail,
            retryable=False,
        )
        return _completed_wagon_or_raise(failed)


__all__ = ["PassageCaptureError", "capture_passage_weight_and_plate"]
