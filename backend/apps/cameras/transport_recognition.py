"""Read one transport number from a requested main-stream frame; no accounting."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from http.client import HTTPException

from . import ai

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
