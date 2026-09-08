"""The automatic client consumes the fresh-frame CV contract, not snapshots."""

import base64
from datetime import timedelta
from io import BytesIO
from unittest.mock import Mock

import pytest
from django.utils import timezone
from PIL import Image

from apps.cameras import ai, transport_recognition
from apps.cameras.tests.test_shipping_tracking import body_tracking


def _detection(model, number=None):
    if model == "wagon_number":
        number = number or "00123455"
        ocr = {
            "digits": number,
            "accepted": True,
            "confidence": 0.96,
            "length_valid": True,
            "checksum_valid": True,
        }
    else:
        number = number or "123ABC02"
        ocr = {"number": number, "accepted": True, "confidence": 0.96}
    return {"number": number, "confidence": 0.91, "ocr": ocr}


def _payload(model, now, *, detections=None):
    return {
        "schema_version": 1,
        "ok": True,
        "status": "observed",
        "camera": "cam7",
        "source": "main",
        "recognition_model": model,
        "observed_at": now.isoformat(),
        "frame_id": "cam7:capture-generation:12",
        "ocr": True,
        "task": "wagon_number_recognition"
        if model == "wagon_number"
        else "vehicle_plate_recognition",
        "detections": [_detection(model)] if detections is None else detections,
    }


@pytest.fixture
def observe(monkeypatch):
    now = timezone.now()
    monkeypatch.setattr(ai, "AI_KEY", "test-key-only")
    monkeypatch.setattr(transport_recognition.timezone, "now", lambda: now)
    direct_frame = Mock(
        side_effect=AssertionError("automatic observations must use CV capture fencing")
    )
    request = Mock()
    monkeypatch.setattr(ai, "camera_frame_jpeg", direct_frame)
    monkeypatch.setattr(ai, "_request", request)

    def run(payload, model="vehicle_number", status=200):
        request.return_value = (status, payload)
        return transport_recognition.observe_transport("cam7", model)

    return run, now, request, direct_frame


@pytest.mark.parametrize("model", ["vehicle_number", "wagon_number"])
def test_exact_cv_contract_preserves_identity_capture_time_and_model(observe, model):
    run, now, request, direct_frame = observe
    payload = _payload(model, now)
    result = run(payload, model)
    assert result.number == payload["detections"][0]["number"]
    assert result.observed_at == now
    assert result.frame_id == payload["frame_id"]
    assert result.snapshot is None
    assert result.tracking["presence"] == "unknown"
    request.assert_called_once_with(
        "POST",
        "/cameras/cam7/transport-observation",
        body={"recognition_model": model},
        timeout_seconds=ai.WAGON_PLATE_TIMEOUT,
    )
    direct_frame.assert_not_called()


def test_body_tracking_is_consumed_from_the_exact_ocr_frame(observe):
    run, now, *_rest = observe
    payload = _payload("vehicle_number", now)
    payload["tracking"] = body_tracking(now)
    assert run(payload).tracking == payload["tracking"]
    payload["tracking"]["observed_at"] = (now - timedelta(seconds=1)).isoformat()
    with pytest.raises(ai.AiProtocolError):
        run(payload)


def test_requested_zone_must_be_acknowledged_by_cv(observe):
    _, now, request, _ = observe
    payload = _payload("vehicle_number", now)
    zone = [0.1, 0.2, 0.8, 0.9]
    request.return_value = (200, payload)
    with pytest.raises(ai.AiProtocolError):
        transport_recognition.observe_transport("cam7", "vehicle_number", zone=zone)
    payload["zone"] = zone
    assert transport_recognition.observe_transport("cam7", "vehicle_number", zone=zone).number == "123ABC02"
    assert request.call_args.kwargs["body"]["zone"] == zone


def test_ocr_failure_does_not_hide_a_present_transport(observe):
    run, now, *_rest = observe
    payload = _payload("vehicle_number", now, detections=[])
    payload["ocr"] = False
    payload["tracking"] = body_tracking(now, number_associated=False)
    result = run(payload)
    assert result.number is None
    assert result.tracking["presence"] == "present"
    assert result.tracking["motion"] == "stationary"


def test_rejected_wagon_checksum_cannot_authorize_body_number_association(observe):
    run, now, *_rest = observe
    payload = _payload("wagon_number", now)
    payload["tracking"] = body_tracking(now)
    payload["detections"][0]["ocr"]["checksum_valid"] = False
    result = run(payload, "wagon_number")
    assert result.number is None
    assert result.tracking["number_associated"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("schema_version", "1"),
        ("camera", "cam8"),
        ("source", "sub"),
        ("source", None),
        ("recognition_model", "wagon_number"),
        ("ok", False),
        ("ocr", "yes"),
        ("task", "wagon_number_recognition"),
        ("detections", {}),
    ],
)
def test_wrong_identity_or_detection_contract_is_not_an_empty_observation(
    observe, field, value
):
    run, now, *_rest = observe
    payload = _payload("vehicle_number", now)
    payload[field] = value
    with pytest.raises(ai.AiProtocolError):
        run(payload)


@pytest.mark.parametrize(
    "value", [None, "", "not-a-time", "2026-99-99T01:00:00Z", "2026-09-08T07:00:00"]
)
def test_capture_timestamp_must_be_valid_and_timezone_aware(observe, value):
    run, now, *_rest = observe
    payload = _payload("vehicle_number", now)
    payload["observed_at"] = value
    with pytest.raises(ai.AiError) as error:
        run(payload)
    assert error.value.status == 503


@pytest.mark.parametrize("offset", [-16, -6, 6])
def test_stale_or_future_capture_is_rejected(observe, offset):
    run, now, *_rest = observe
    with pytest.raises(ai.AiError) as error:
        run(_payload("vehicle_number", now + timedelta(seconds=offset)))
    assert error.value.status == 503


def test_slow_response_cannot_refresh_the_original_capture_timestamp(
    observe, monkeypatch
):
    run, now, *_rest = observe
    moments = iter([now, now + timedelta(seconds=16)])
    monkeypatch.setattr(transport_recognition.timezone, "now", lambda: next(moments))
    with pytest.raises(ai.AiError) as error:
        run(_payload("vehicle_number", now))
    assert error.value.status == 503


@pytest.mark.parametrize("frame_id", [None, "", 12, "x" * 257])
def test_missing_or_unbounded_frame_identity_is_rejected(observe, frame_id):
    run, now, *_rest = observe
    payload = _payload("vehicle_number", now)
    payload["frame_id"] = frame_id
    with pytest.raises(ai.AiError):
        run(payload)


def test_repeated_frame_id_is_preserved_for_durable_pipeline_deduplication(observe):
    run, now, *_rest = observe
    payload = _payload("vehicle_number", now)
    first, repeated = run(payload), run(payload)
    assert first == repeated
    assert first.frame_id == payload["frame_id"]


@pytest.mark.parametrize("model", ["vehicle_number", "wagon_number"])
def test_empty_or_ambiguous_observation_cannot_choose_a_number(observe, model):
    run, now, *_rest = observe
    assert run(_payload(model, now, detections=[]), model).number is None
    numbers = (
        ["00123455", "12345674"]
        if model == "wagon_number"
        else ["123ABC02", "777ABC02"]
    )
    payload = _payload(
        model, now, detections=[_detection(model, number) for number in numbers]
    )
    assert run(payload, model).number is None


def test_wagon_uses_ocr_digits_and_requires_checksum_on_its_detection(observe):
    run, now, *_rest = observe
    payload = _payload("wagon_number", now)
    payload["detections"][0]["ocr"]["checksum_valid"] = False
    assert run(payload, "wagon_number").number is None
    payload["detections"][0]["ocr"]["checksum_valid"] = True
    payload["detections"][0]["ocr"]["digits"] = "12345674"
    with pytest.raises(ai.AiProtocolError):
        run(payload, "wagon_number")


def test_valid_same_frame_jpeg_is_decoded_without_fetching_another_frame(observe):
    run, now, request, direct_frame = observe
    image = BytesIO()
    Image.new("RGB", (4, 3), (30, 80, 150)).save(image, format="JPEG")
    jpeg = image.getvalue()
    payload = _payload("vehicle_number", now)
    payload["snapshot_jpeg_base64"] = base64.b64encode(jpeg).decode("ascii")
    result = run(payload)
    assert result.snapshot == jpeg
    assert result.frame_id == payload["frame_id"]
    assert result.observed_at == now
    direct_frame.assert_not_called()
    assert request.call_count == 1


@pytest.mark.parametrize(
    "encoded",
    [
        True,
        "!not-base64!",
        "",
        base64.b64encode(b"not JPEG").decode(),
        base64.b64encode(b"\xff\xd8\xff" + b"x" * (512 * 1024)).decode(),
        "A" * 700_001,
    ],
)
def test_malformed_or_oversized_evidence_fails_closed(observe, encoded):
    run, now, *_rest = observe
    payload = _payload("vehicle_number", now)
    payload["snapshot_jpeg_base64"] = encoded
    with pytest.raises(ai.AiProtocolError):
        run(payload)


@pytest.mark.parametrize("status", [401, 404, 409, 429, 503])
def test_upstream_error_preserves_status_without_leaking_runtime_details(
    observe, status
):
    run, now, *_rest = observe
    with pytest.raises(ai.AiError) as error:
        run({"error": "private camera credentials"}, status=status)
    assert error.value.status == status
    assert "private" not in str(error.value)
