"""Read one transport number from a requested main-stream frame; no accounting."""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.client import HTTPException

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import ai
from .shipping_tracking import unknown_tracking, validate_tracking

_MODELS = {
    "vehicle_number": ("/vehicle-number/detect", "vehicle_plate_recognition"),
    "wagon_number": ("/wagon-number/detect", "wagon_number_recognition"),
}
_WAGON_NUMBER = re.compile(r"[0-9]{8}")


def _invalid_response() -> ai.AiProtocolError:
    return ai.AiProtocolError(
        "Модель вернула некорректный результат распознавания номера"
    )


def _validate_confidence(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 1
        or not math.isfinite(value)
    ):
        raise _invalid_response()


def _accepted_number(detection: object, recognition_model: str) -> str | None:
    if not isinstance(detection, Mapping):
        raise _invalid_response()
    ocr = detection.get("ocr")
    if not isinstance(ocr, Mapping) or not isinstance(ocr.get("accepted"), bool):
        raise _invalid_response()
    if not ocr["accepted"]:
        return None

    # Both upstream services put the canonical number on this detection.
    # Wagon OCR supplies `digits`, not `number`; the top-level `number` is only
    # the first detection and must never stand in for another plate's result.
    number = detection.get("number")
    if not isinstance(number, str) or not number:
        raise _invalid_response()
    ocr_number = ocr.get("digits" if recognition_model == "wagon_number" else "number")
    if not isinstance(ocr_number, str) or ocr_number != number:
        raise _invalid_response()
    _validate_confidence(detection.get("confidence"))
    _validate_confidence(ocr.get("confidence"))

    if recognition_model == "wagon_number":
        length_valid = ocr.get("length_valid")
        checksum_valid = ocr.get("checksum_valid")
        if (
            not isinstance(length_valid, bool)
            or "checksum_valid" not in ocr
            or (checksum_valid is not None and not isinstance(checksum_valid, bool))
        ):
            raise _invalid_response()
        # Diagnostic wagon OCR marks any sufficiently confident digit string
        # accepted. Match its automatic consensus by requiring the declared
        # length and checksum as well before offering a number to the operator.
        if not length_valid or checksum_valid is not True:
            return None
        if _WAGON_NUMBER.fullmatch(number) is None:
            raise _invalid_response()
    elif ai.VEHICLE_PLATE_RE.fullmatch(number) is None:
        raise _invalid_response()
    return number


def recognize_transport_number(camera: str, recognition_model: str) -> str | None:
    """Run a manual OCR check without binding an order or interpreting presence.

    ``None`` means there is no single accepted number. It does not mean that
    the loading position is empty. An unavailable frame/model or a malformed
    response raises an AI error, preserving that distinction for the caller.
    """
    camera = ai.camera_id(camera)
    if not isinstance(recognition_model, str) or recognition_model not in _MODELS:
        raise ai.AiError(400, "Выберите модель номера машины или вагона")
    if not ai.enabled():
        raise ai.AiError(503, "AI-сервис камер не настроен")

    # go2rtc's camN alias is sub; camNmain is the provisioned main stream.
    # This helper already bounds JPEG bytes and timeout and never reads an
    # archived recording or a previously recognized vehicle event.
    try:
        frame = ai.camera_frame_jpeg(f"{camera}main")
    except (HTTPException, TimeoutError, OSError) as exc:
        raise ai.AiUnavailable("Не удалось получить кадр выбранной камеры") from exc
    if frame is None:
        raise ai.AiUnavailable("Не удалось получить кадр выбранной камеры")

    path, expected_task = _MODELS[recognition_model]
    response_status, payload = ai._request(
        "POST",
        path,
        raw_body=frame,
        content_type="image/jpeg",
        timeout_seconds=ai.WAGON_PLATE_TIMEOUT,
    )
    if response_status != 200:
        raise ai.AiError(response_status, "Модель распознавания номера недоступна")
    return number_from_payload(payload, recognition_model)


def number_from_payload(payload: object, recognition_model: str) -> str | None:
    """Shared strict OCR acceptance for diagnostics and automatic observations."""
    _, expected_task = _MODELS[recognition_model]
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise _invalid_response()
    if payload.get("ocr") is False:
        raise ai.AiError(
            503, "Распознавание текста номера не включено в выбранной модели"
        )
    if payload.get("ocr") is not True or payload.get("task") != expected_task:
        raise _invalid_response()
    detections = payload.get("detections")
    if not isinstance(detections, list):
        raise _invalid_response()
    numbers = {
        number
        for detection in detections
        if (number := _accepted_number(detection, recognition_model)) is not None
    }
    return next(iter(numbers)) if len(numbers) == 1 else None


@dataclass(frozen=True)
class TransportObservation:
    number: str | None
    observed_at: datetime
    frame_id: str
    snapshot: bytes | None = None
    tracking: dict = field(default_factory=unknown_tracking)


def observe_transport(camera: str, recognition_model: str, *, zone=None) -> TransportObservation:
    """Ask the camera PC for a newly captured main frame, never a cached JPEG.

    This API does not alter the grain/scale monitors or infer vehicle departure.
    """
    camera = ai.camera_id(camera)
    if recognition_model not in _MODELS:
        raise ai.AiError(400, "Выберите модель номера машины или вагона")
    if not ai.enabled():
        raise ai.AiError(503, "AI-сервис камер не настроен")
    requested_at = timezone.now()
    status, payload = ai._request(
        "POST",
        f"/cameras/{camera}/transport-observation",
        body={"recognition_model": recognition_model, **({"zone": zone} if zone is not None else {})},
        timeout_seconds=ai.WAGON_PLATE_TIMEOUT,
    )
    if status != 200:
        raise ai.AiError(status, "Автоматическое распознавание на ПК камер недоступно")
    if (
        not isinstance(payload, Mapping)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or payload.get("camera") != camera
        or payload.get("source") != "main"
        or payload.get("recognition_model") != recognition_model
        or (zone is not None and payload.get("zone") != zone)
    ):
        raise _invalid_response()
    raw_time = payload.get("observed_at")
    try:
        observed_at = parse_datetime(raw_time) if isinstance(raw_time, str) else None
    except ValueError:
        observed_at = None
    frame_id = payload.get("frame_id")
    now = timezone.now()
    if (
        observed_at is None
        or timezone.is_naive(observed_at)
        or observed_at
        < max(requested_at - timedelta(seconds=5), now - timedelta(seconds=15))
        or observed_at > now + timedelta(seconds=5)
        or not isinstance(frame_id, str)
        or not 1 <= len(frame_id) <= 256
    ):
        raise ai.AiError(
            503, "ПК камер не подтвердил свежий кадр; проверьте камеру и время на ПК"
        )
    tracking = validate_tracking(payload.get("tracking"), observed_at)
    # A body can be observed while the separate OCR model is unavailable.
    if payload.get("ocr") is False:
        if (
            payload.get("ok") is not True
            or payload.get("task") != _MODELS[recognition_model][1]
            or payload.get("detections") != []
        ):
            raise _invalid_response()
        number = None
    else:
        number = number_from_payload(payload, recognition_model)
    if number is None:
        tracking["number_associated"] = False
    snapshot = None
    encoded = payload.get("snapshot_jpeg_base64")
    if encoded is not None:
        try:
            if not isinstance(encoded, str) or len(encoded) > 700_000:
                raise ValueError
            snapshot = base64.b64decode(encoded, validate=True)
            if not snapshot.startswith(b"\xff\xd8\xff") or len(snapshot) > 512 * 1024:
                raise ValueError
        except (ValueError, binascii.Error) as exc:
            raise _invalid_response() from exc
    return TransportObservation(number, observed_at, frame_id, snapshot, tracking)
