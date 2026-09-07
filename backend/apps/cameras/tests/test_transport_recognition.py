"""Manual shipping OCR consumes the actual vehicle/wagon diagnostic contract."""

from http.client import IncompleteRead
from unittest.mock import patch

import pytest

from apps.cameras import ai
from apps.cameras.transport_recognition import recognize_transport_number

FRAME = b"\xff\xd8\xff\xe0camera-main-frame"


def _vehicle(number="123ABC02", *, accepted=True):
    return {
        "class_name": "license_plate",
        "confidence": 0.91,
        "number": number if accepted else None,
        "ocr": {
            "number": number if accepted else "",
            "accepted": accepted,
            "confidence": 0.96,
        },
    }


def _wagon(number="00123455", *, length_valid=True, checksum_valid=True):
    return {
        "class_name": "wagon_plate",
        "confidence": 0.91,
        "number": number,
        "ocr": {
            "digits": number,
            "accepted": True,
            "length_valid": length_valid,
            "checksum_valid": checksum_valid,
            "confidence": 0.96,
        },
    }


def _payload(model, detections):
    return {
        "ok": True,
        "ocr": True,
        "task": "wagon_number_recognition"
        if model == "wagon_number"
        else "vehicle_plate_recognition",
        "detections": detections,
        "number": next(
            (item["number"] for item in detections if item.get("number")), None
        ),
    }


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "unit-test-only")


def _recognize(payload, model="vehicle_number"):
    with (
        patch.object(ai, "camera_frame_jpeg", return_value=FRAME),
        patch.object(ai, "_request", return_value=(200, payload)),
    ):
        return recognize_transport_number("cam7", model)


@pytest.mark.parametrize(
    "model,detection,path,number",
    [
        ("vehicle_number", _vehicle(), "/vehicle-number/detect", "123ABC02"),
        ("wagon_number", _wagon(), "/wagon-number/detect", "00123455"),
    ],
)
def test_uses_main_frame_and_exact_stateless_diagnostic_endpoint(
    model, detection, path, number
):
    with (
        patch.object(ai, "camera_frame_jpeg", return_value=FRAME) as frame,
        patch.object(
            ai, "_request", return_value=(200, _payload(model, [detection]))
        ) as request,
    ):
        assert recognize_transport_number("cam7", model) == number
    frame.assert_called_once_with("cam7main")
    request.assert_called_once_with(
        "POST",
        path,
        raw_body=FRAME,
        content_type="image/jpeg",
        timeout_seconds=ai.WAGON_PLATE_TIMEOUT,
    )


@pytest.mark.parametrize("number", ["160AL17", "X209LAN", "001ABC02"])
def test_preserves_supported_vehicle_formats(number):
    assert _recognize(_payload("vehicle_number", [_vehicle(number)])) == number


@pytest.mark.parametrize("model", ["vehicle_number", "wagon_number"])
def test_empty_detection_result_has_no_number(model):
    assert _recognize(_payload(model, []), model) is None


def test_rejected_ocr_does_not_use_top_level_or_raw_candidate():
    detection = _vehicle(accepted=False)
    detection["ocr"]["raw_text"] = "777ABC02"
    payload = _payload("vehicle_number", [detection])
    payload["number"] = "777ABC02"
    assert _recognize(payload) is None


@pytest.mark.parametrize(
    "model,detections",
    [
        ("vehicle_number", [_vehicle(), _vehicle("777ABC02")]),
        ("wagon_number", [_wagon(), _wagon("12345674")]),
    ],
)
def test_multiple_distinct_accepted_numbers_are_ambiguous(model, detections):
    assert _recognize(_payload(model, detections), model) is None


def test_repeated_detection_of_the_same_number_is_unambiguous():
    assert (
        _recognize(_payload("vehicle_number", [_vehicle(), _vehicle()])) == "123ABC02"
    )


@pytest.mark.parametrize(
    "detection",
    [
        _wagon("1234567", length_valid=False, checksum_valid=None),
        _wagon(checksum_valid=False),
    ],
)
def test_wagon_rejection_flags_are_honored(detection):
    assert _recognize(_payload("wagon_number", [detection]), "wagon_number") is None


@pytest.mark.parametrize("number", [12345678, True, None, ""])
def test_accepted_number_must_be_a_nonempty_string(number):
    detection = _wagon()
    detection["number"] = number
    detection["ocr"]["digits"] = number
    with pytest.raises(ai.AiProtocolError):
        _recognize(_payload("wagon_number", [detection]), "wagon_number")


def test_wagon_number_must_belong_to_the_accepted_detection():
    first = _wagon()
    second = _wagon("12345674")
    del second["number"]
    with pytest.raises(ai.AiProtocolError):
        _recognize(_payload("wagon_number", [first, second]), "wagon_number")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ok", False),
        ("ocr", "yes"),
        ("task", "wagon_number_recognition"),
        ("detections", None),
    ],
)
def test_malformed_response_is_not_reported_as_an_empty_position(field, value):
    payload = _payload("vehicle_number", [_vehicle()])
    payload[field] = value
    with pytest.raises(ai.AiProtocolError):
        _recognize(payload)


@pytest.mark.parametrize("detection", [None, {}, {"ocr": {"accepted": 1}}])
def test_malformed_detection_is_not_silently_skipped(detection):
    payload = _payload("vehicle_number", [])
    payload["detections"] = [detection]
    with pytest.raises(ai.AiProtocolError):
        _recognize(payload)


@pytest.mark.parametrize("confidence", [True, -1, 2, float("nan"), float("inf"), "0.9"])
def test_invalid_accepted_confidence_is_rejected(confidence):
    detection = _vehicle()
    detection["ocr"]["confidence"] = confidence
    with pytest.raises(ai.AiProtocolError):
        _recognize(_payload("vehicle_number", [detection]))


def test_mismatched_canonical_ocr_number_is_rejected():
    detection = _vehicle()
    detection["ocr"]["number"] = "777ABC02"
    with pytest.raises(ai.AiProtocolError):
        _recognize(_payload("vehicle_number", [detection]))


def test_detector_without_ocr_is_unavailable():
    payload = _payload("wagon_number", [])
    payload.update(ocr=False, task="wagon_plate_detection")
    with pytest.raises(ai.AiError) as error:
        _recognize(payload, "wagon_number")
    assert error.value.status == 503


def test_no_camera_frame_does_not_call_model_or_claim_no_vehicle():
    with (
        patch.object(ai, "camera_frame_jpeg", return_value=None),
        patch.object(ai, "_request") as request,
        pytest.raises(ai.AiUnavailable),
    ):
        recognize_transport_number("cam7", "vehicle_number")
    request.assert_not_called()


def test_interrupted_camera_response_is_an_unavailable_frame():
    with (
        patch.object(ai, "camera_frame_jpeg", side_effect=IncompleteRead(b"partial")),
        patch.object(ai, "_request") as request,
        pytest.raises(ai.AiUnavailable),
    ):
        recognize_transport_number("cam7", "vehicle_number")
    request.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 404, 503])
def test_upstream_failures_keep_status_without_private_details(status):
    with (
        patch.object(ai, "camera_frame_jpeg", return_value=FRAME),
        patch.object(
            ai, "_request", return_value=(status, {"error": "private runtime path"})
        ),
        pytest.raises(ai.AiError) as error,
    ):
        recognize_transport_number("cam7", "vehicle_number")
    assert error.value.status == status
    assert "private" not in str(error.value)


def test_network_failure_propagates():
    with (
        patch.object(ai, "camera_frame_jpeg", return_value=FRAME),
        patch.object(ai, "_request", side_effect=ai.AiUnavailable("unavailable")),
        pytest.raises(ai.AiUnavailable),
    ):
        recognize_transport_number("cam7", "vehicle_number")


@pytest.mark.parametrize(
    "camera,model",
    [("7", "vehicle_number"), ("../cam1", "wagon_number"), ("cam7", "other")],
)
def test_invalid_camera_or_model_is_rejected_before_camera_access(camera, model):
    with patch.object(ai, "camera_frame_jpeg") as frame, pytest.raises(ai.AiError):
        recognize_transport_number(camera, model)
    frame.assert_not_called()


def test_missing_configuration_is_reported_before_camera_access(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "")
    with (
        patch.object(ai, "camera_frame_jpeg") as frame,
        pytest.raises(ai.AiError) as error,
    ):
        recognize_transport_number("cam7", "vehicle_number")
    assert error.value.status == 503
    frame.assert_not_called()
