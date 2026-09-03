from unittest.mock import patch

import pytest

from apps.cameras import ai

REQUEST_ID = "4fbd9ed6-0c61-4a2e-8d14-dac48fef4cbe"
TRIGGER = "2026-08-30T10:21:14.381000Z"


def _recognized_payload(**overrides):
    payload = {
        "ok": True,
        "status": "recognized",
        "request_id": REQUEST_ID,
        "camera": "cam1",
        "source": "main",
        "stable_weight_at": TRIGGER,
        "recognized_at": "2026-08-30T10:21:15.001Z",
        "vehicle_number": "123ABC02",
        "confirmation": {
            "votes": 3,
            "detector_confidence": 0.91,
            "ocr_confidence": 0.96,
        },
        "frames_scanned": 3,
    }
    payload.update(overrides)
    return payload


def _production_payload(**overrides):
    payload = {
        "status": "recognized",
        "vehicle_number": "123ABC02",
        "confirmation": {
            "votes": 3,
            "detector_confidence": 0.91,
            "ocr_confidence": 0.96,
        },
        "frames_scanned": 3,
    }
    payload.update(overrides)
    return payload


def test_weight_triggered_client_sends_exact_path_body_key_and_timeout(settings):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS = 12
    payload = _production_payload()

    with patch.object(ai, "_request", return_value=(200, payload)) as request:
        result = ai.recognize_vehicle_from_camera("cam1", REQUEST_ID, TRIGGER)

    assert result == {
        **payload,
        "ok": True,
        "request_id": REQUEST_ID,
        "camera": "cam1",
        "source": "main",
        "stable_weight_at": TRIGGER,
        "recognized_at": result["recognized_at"],
    }
    assert result["recognized_at"].endswith("Z")
    request.assert_called_once_with(
        "POST",
        "/cameras/cam1/vehicle-recognition",
        {"stable_weight_at": TRIGGER},
        timeout_seconds=12,
        idempotency_key=REQUEST_ID,
    )


def test_weight_triggered_retry_uses_lookup_only_endpoint(settings):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS = 12
    payload = _production_payload()

    with patch.object(ai, "_request", return_value=(200, payload)) as request:
        result = ai.retry_vehicle_recognition_from_camera(
            "cam1", REQUEST_ID, TRIGGER
        )

    assert result["request_id"] == REQUEST_ID
    assert result["camera"] == "cam1"
    assert result["source"] == "main"
    assert result["stable_weight_at"] == TRIGGER
    assert result["recognized_at"].endswith("Z")
    request.assert_called_once_with(
        "POST",
        "/cameras/cam1/vehicle-recognition-retry",
        {"stable_weight_at": TRIGGER},
        timeout_seconds=12,
        idempotency_key=REQUEST_ID,
    )


def test_weight_triggered_client_requires_the_configured_roi_source(settings):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "sub"

    with (
        patch.object(
            ai,
            "_request",
            return_value=(200, _recognized_payload(source="main")),
        ),
        pytest.raises(ai.AiProtocolError),
    ):
        ai.recognize_vehicle_from_camera("cam1", REQUEST_ID, TRIGGER)

    with patch.object(
        ai,
        "_request",
        return_value=(200, _recognized_payload(source="sub")),
    ):
        result = ai.recognize_vehicle_from_camera("cam1", REQUEST_ID, TRIGGER)

    assert result["source"] == "sub"


def test_weight_triggered_client_preserves_terminal_payload():
    payload = {
        "ok": False,
        "status": "no_match",
        "error": "vehicle number was not confirmed",
        "retryable": False,
    }
    with (
        patch.object(ai, "_request", return_value=(422, payload)),
        pytest.raises(ai.AiError) as exc_info,
    ):
        ai.recognize_vehicle_from_camera("cam1", REQUEST_ID, TRIGGER)

    assert exc_info.value.status == 422
    assert exc_info.value.payload == payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"ok": False},
        {"request_id": "17d95309-91ff-48c8-8ac4-43dc70936f1a"},
        {"camera": "cam2"},
        {"stable_weight_at": "2026-08-30T10:21:13.000000Z"},
        {"vehicle_number": "not-a-plate"},
        {"source": "unknown"},
        {"frames_scanned": 0},
        {"frames_scanned": True},
        {
            "confirmation": {
                "votes": 0,
                "detector_confidence": 0.9,
                "ocr_confidence": 0.9,
            }
        },
        {
            "confirmation": {
                "votes": 3,
                "detector_confidence": 1.1,
                "ocr_confidence": 0.9,
            }
        },
    ],
)
def test_weight_triggered_client_rejects_malformed_success(overrides):
    with (
        patch.object(
            ai,
            "_request",
            return_value=(200, _recognized_payload(**overrides)),
        ),
        pytest.raises(ai.AiUnavailable),
    ):
        ai.recognize_vehicle_from_camera("cam1", REQUEST_ID, TRIGGER)


@pytest.mark.parametrize(
    "request_id",
    ["", REQUEST_ID.upper(), "4fbd9ed6-0c61-4a2e-8d14-dac48fef4cbe "],
)
def test_weight_triggered_client_requires_canonical_uuid(request_id):
    with pytest.raises(ValueError), patch.object(ai, "_request") as request:
        ai.recognize_vehicle_from_camera("cam1", request_id, TRIGGER)
    request.assert_not_called()
