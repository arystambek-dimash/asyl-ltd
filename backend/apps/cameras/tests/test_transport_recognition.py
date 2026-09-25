"""Strict OCR acceptance of the actual vehicle/wagon number-model contract."""

from unittest.mock import patch

import pytest

from apps.cameras import ai
from apps.cameras.tests.shipping_fakes import recognition_payload, vehicle_plate, wagon_plate

FRAME = b"\xff\xd8\xff\xe0camera-main-frame"


def _recognize(payload, model="vehicle_number"):
    return ai.number_from_payload(payload, model)


@pytest.mark.parametrize(
    "model,detection,path,number",
    [
        ("vehicle_number", vehicle_plate(), "/vehicle-number/detect", "123ABC02"),
        ("wagon_number", wagon_plate(), "/wagon-number/detect", "00123455"),
    ],
)
def test_detect_number_posts_frame_to_exact_stateless_model_endpoint(
    model, detection, path, number
):
    with patch.object(
        ai, "_request", return_value=(200, recognition_payload(model, [detection]))
    ) as request:
        assert _recognize(ai.detect_number(model, FRAME), model) == number
    request.assert_called_once_with(
        "POST",
        path,
        raw_body=FRAME,
        content_type="image/jpeg",
        timeout_seconds=ai.WAGON_PLATE_TIMEOUT,
    )


@pytest.mark.parametrize("number", ["160AL17", "X209LAN", "001ABC02"])
def test_preserves_supported_vehicle_formats(number):
    assert _recognize(recognition_payload("vehicle_number", [vehicle_plate(number)])) == number


@pytest.mark.parametrize("model", ["vehicle_number", "wagon_number"])
def test_empty_detection_result_has_no_number(model):
    assert _recognize(recognition_payload(model, []), model) is None


def test_rejected_ocr_does_not_use_top_level_or_raw_candidate():
    detection = vehicle_plate(accepted=False)
    detection["ocr"]["raw_text"] = "777ABC02"
    payload = recognition_payload("vehicle_number", [detection])
    payload["number"] = "777ABC02"
    assert _recognize(payload) is None


@pytest.mark.parametrize(
    "model,detections",
    [
        ("vehicle_number", [vehicle_plate(), vehicle_plate("777ABC02")]),
        ("wagon_number", [wagon_plate(), wagon_plate("12345674")]),
    ],
)
def test_multiple_distinct_accepted_numbers_are_ambiguous(model, detections):
    assert _recognize(recognition_payload(model, detections), model) is None


def test_repeated_detection_of_the_same_number_is_unambiguous():
    assert (
        _recognize(recognition_payload("vehicle_number", [vehicle_plate(), vehicle_plate()])) == "123ABC02"
    )


@pytest.mark.parametrize(
    "detection",
    [
        wagon_plate("1234567", length_valid=False, checksum_valid=None),
        wagon_plate(checksum_valid=False),
    ],
)
def test_wagon_rejection_flags_are_honored(detection):
    assert _recognize(recognition_payload("wagon_number", [detection]), "wagon_number") is None


@pytest.mark.parametrize("number", [12345678, True, None, ""])
def test_accepted_number_must_be_a_nonempty_string(number):
    detection = wagon_plate()
    detection["number"] = number
    detection["ocr"]["digits"] = number
    with pytest.raises(ai.AiProtocolError):
        _recognize(recognition_payload("wagon_number", [detection]), "wagon_number")


def test_wagon_number_must_belong_to_the_accepted_detection():
    first = wagon_plate()
    second = wagon_plate("12345674")
    del second["number"]
    with pytest.raises(ai.AiProtocolError):
        _recognize(recognition_payload("wagon_number", [first, second]), "wagon_number")


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
    payload = recognition_payload("vehicle_number", [vehicle_plate()])
    payload[field] = value
    with pytest.raises(ai.AiProtocolError):
        _recognize(payload)


@pytest.mark.parametrize("detection", [None, {}, {"ocr": {"accepted": 1}}])
def test_malformed_detection_is_not_silently_skipped(detection):
    payload = recognition_payload("vehicle_number", [])
    payload["detections"] = [detection]
    with pytest.raises(ai.AiProtocolError):
        _recognize(payload)


@pytest.mark.parametrize("confidence", [True, -1, 2, float("nan"), float("inf"), "0.9"])
def test_invalid_accepted_confidence_is_rejected(confidence):
    detection = vehicle_plate()
    detection["ocr"]["confidence"] = confidence
    with pytest.raises(ai.AiProtocolError):
        _recognize(recognition_payload("vehicle_number", [detection]))


def test_mismatched_canonical_ocr_number_is_rejected():
    detection = vehicle_plate()
    detection["ocr"]["number"] = "777ABC02"
    with pytest.raises(ai.AiProtocolError):
        _recognize(recognition_payload("vehicle_number", [detection]))


def test_detector_without_ocr_is_unavailable():
    payload = recognition_payload("wagon_number", [])
    payload.update(ocr=False, task="wagon_plate_detection")
    with pytest.raises(ai.AiError) as error:
        _recognize(payload, "wagon_number")
    assert error.value.status == 503


@pytest.mark.parametrize("status", [400, 401, 404, 503])
def test_upstream_failures_keep_status_without_private_details(status):
    with (
        patch.object(
            ai, "_request", return_value=(status, {"error": "private runtime path"})
        ),
        pytest.raises(ai.AiError) as error,
    ):
        ai.detect_number("vehicle_number", FRAME)
    assert error.value.status == status
    assert "private" not in str(error.value)
