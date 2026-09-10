import base64
import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.db import connection
from django.utils import timezone
from PIL import Image

from apps.cameras import ai, shipping_segment_identity as identity, shipping_segments
from apps.cameras.models import (
    AlwaysOnCounterCursor, AlwaysOnImportedEvent, ShippingLoadingEvent,
    ShippingLoadingSegment, ShippingLoadingSession, ShippingSessionSettings, ShippingTransportCamera,
)
from apps.cameras.tests.test_transport_recognition import _payload, _vehicle, _wagon

pytestmark = pytest.mark.django_db(transaction=True)
JPEG = b"\xff\xd8\xff\xe0one original loading frame"


@pytest.fixture(autouse=True)
def configuration(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.OPENAI_API_KEY = "unit-test-only"
    settings.WEIGHING_AI_MODEL = "gpt-5-mini"
    settings.GO2RTC_API_URL = "http://relay.example.test:1984"
    with patch.object(identity.http.client, "HTTPSConnection", side_effect=AssertionError("Unexpected external API request")), patch.object(identity.urllib.request, "urlopen", side_effect=AssertionError("Unexpected live camera request")):
        yield


def segment(*, age=0, model="vehicle_number", number_camera="cam7", camera="cam3", loading_zone=None):
    at = timezone.now() - timedelta(seconds=age)
    AlwaysOnCounterCursor.objects.create(camera=camera, last_event_id=1, last_total=1, event_compat_total=1)
    event = AlwaysOnImportedEvent.objects.create(
        camera=camera, upstream_event_id=1, occurred_at=at, source="sub", mode="always_on",
        analytics_scope="shipping", applied_to_analytics=True,
    )
    session = ShippingLoadingSession.objects.create(
        camera=camera, started_at=at, last_counted_at=at, total_bags=1,
    )
    result = ShippingLoadingSegment.objects.create(
        session=session, camera=camera, number_camera=number_camera,
        loading_zone=loading_zone,
        configured_recognition_model=model, recognition_model=model,
        started_at=at, last_counted_at=at, total_bags=1,
        first_event=event, last_event=event, first_upstream_event_id=1, last_upstream_event_id=1,
    )
    ShippingLoadingEvent.objects.create(event=event, segment=result)
    return result


def photographed(**kwargs):
    row = segment(**kwargs)
    with patch.object(identity, "capture_frame", return_value=JPEG):
        assert identity.capture_once(row.pk)
    row.refresh_from_db()
    assert row.photo.read() == JPEG
    return row


@pytest.mark.parametrize("model,detection,path,number", [
    ("vehicle_number", _vehicle(), "/vehicle-number/detect", "123ABC02"),
])
def test_one_primary_uses_saved_photo_and_never_repeats_during_segment(model, detection, path, number):
    row = photographed(model=model)

    def primary(*args, **kwargs):
        assert not connection.in_atomic_block
        assert ShippingLoadingSegment.objects.get(pk=row.pk).primary_attempted
        assert kwargs["raw_body"] == JPEG
        return 200, _payload(model, [detection])

    with patch.object(ai, "_request", side_effect=primary) as request, patch.object(identity, "gpt_number") as gpt, patch.object(identity, "capture_frame") as frame:
        assert identity.process_once(row.pk)
        assert not identity.process_once(row.pk)
        assert not identity.capture_once(row.pk)
    request.assert_called_once_with("POST", path, raw_body=JPEG, content_type="image/jpeg", timeout_seconds=ai.WAGON_PLATE_TIMEOUT)
    gpt.assert_not_called()
    frame.assert_not_called()
    row.refresh_from_db()
    assert (row.number, row.number_source, row.identity_status) == (number, "model", "identified")
    assert row.total_bags == row.session.total_bags == 1
    assert ShippingLoadingEvent.objects.count() == 1


def test_wagon_uses_only_openai_once_even_when_native_model_could_read_it():
    row = photographed(model="wagon_number")
    with patch.object(ai, "_request", return_value=(200, _payload("wagon_number", [_wagon()]))) as primary, patch.object(
        identity, "gpt_number", return_value=("00123455", "wagon_number", "wagon-response"),
    ) as gpt, patch.object(identity, "capture_frame") as frame:
        assert identity.process_once(row.pk)
        assert not identity.process_once(row.pk)
    primary.assert_not_called()
    frame.assert_not_called()
    gpt.assert_called_once_with(JPEG, recognition_model="wagon_number")
    row.refresh_from_db()
    assert (row.number, row.number_source, row.identity_status) == ("00123455", "gpt", "identified")
    assert not row.primary_attempted
    assert row.photo.read() == JPEG
    assert row.total_bags == row.session.total_bags == 1


@pytest.mark.parametrize("failure", ["missing_key", "unreadable", "wrong_type", "unavailable"])
def test_wagon_never_falls_back_to_native_and_keeps_counts_on_openai_failure(settings, failure):
    row = photographed(model="wagon_number")
    if failure == "missing_key":
        settings.OPENAI_API_KEY = ""
    verdict = ("123ABC02", "vehicle_number", "response") if failure == "wrong_type" else ("", "unknown", "response")
    with patch.object(ai, "_request") as primary, patch.object(identity, "gpt_number", return_value=verdict, side_effect=TimeoutError if failure == "unavailable" else None) as gpt:
        attempts = 3 if failure == "unavailable" else 1
        for _ in range(attempts):
            ShippingLoadingSegment.objects.filter(pk=row.pk).update(identity_next_attempt_at=timezone.now())
            assert identity.process_once(row.pk)
        assert not identity.process_once(row.pk)
    primary.assert_not_called()
    assert gpt.call_count == (0 if failure == "missing_key" else attempts)
    row.refresh_from_db()
    assert not row.primary_attempted and row.number == ""
    assert row.identity_status == "unidentified"
    assert row.total_bags == row.session.total_bags == 1
    assert row.photo.read() == JPEG


def test_primary_failure_calls_gpt_with_exact_same_original_bytes():
    row = photographed()

    def fallback(frame):
        assert not connection.in_atomic_block
        assert frame == JPEG
        return "123ABC02", "vehicle_number", "response-test"

    with patch.object(ai, "_request", side_effect=ai.AiUnavailable("offline")) as primary, patch.object(identity, "gpt_number", side_effect=fallback) as gpt:
        assert identity.process_once(row.pk)
    assert primary.call_count == gpt.call_count == 1
    row.refresh_from_db()
    assert row.number_source == "gpt"
    assert row.identity_response_id == "response-test"
    assert row.total_bags == 1


def test_primary_and_gpt_share_saved_zone_crop_while_original_photo_is_preserved():
    image = Image.new("RGB", (120, 80), "red")
    image.paste("blue", (60, 0, 120, 80))
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=95, subsampling=0)
    original = stream.getvalue()
    row = segment(loading_zone=[0.5, 0.25, 1.0, 0.75])
    with patch.object(identity, "capture_frame", return_value=original):
        assert identity.capture_once(row.pk)
    # A later settings edit must never select a different region for this
    # segment's already saved evidence, including on fallback retries.
    ShippingTransportCamera.objects.create(
        conveyor_camera="cam3", number_camera="cam7", recognition_model="vehicle_number",
        loading_zone=[0, 0, 0.5, 1],
    )
    with patch.object(ai, "_request", return_value=(200, _payload("vehicle_number", []))) as primary, patch.object(identity, "gpt_number", return_value=("123ABC02", "vehicle_number", "response-test")) as gpt:
        assert identity.process_once(row.pk)
    crop = primary.call_args.kwargs["raw_body"]
    gpt.assert_called_once_with(crop)
    with Image.open(io.BytesIO(crop)) as cropped:
        assert cropped.size == (60, 40)
        red, green, blue = cropped.getpixel((30, 20))
        assert blue > 240 and red < 15 and green < 15
    row.refresh_from_db()
    assert row.photo.read() == original
    assert row.loading_zone == [0.5, 0.25, 1.0, 0.75]
    assert row.number_source == "gpt"
    assert row.total_bags == row.session.total_bags == 1


@pytest.mark.parametrize("zone", [
    [True, 0, 1, 1], [0.8, 0, 0.2, 1], [0, 0, 1.1, 1], [0, 0, 1], {"left": 0},
])
def test_invalid_zone_does_not_silently_recognize_another_transport_from_full_frame(zone):
    row = photographed(loading_zone=zone)
    with patch.object(ai, "_request") as primary, patch.object(identity, "gpt_number") as gpt:
        assert identity.process_once(row.pk)
    primary.assert_not_called()
    gpt.assert_not_called()
    row.refresh_from_db()
    assert row.identity_error == "loading_zone_invalid"
    assert row.identity_status == "unidentified"
    assert row.photo.read() == JPEG
    assert row.total_bags == 1


@pytest.mark.parametrize("model,detections", [
    ("vehicle_number", []),
    ("vehicle_number", [_vehicle(accepted=False)]),
    ("vehicle_number", [_vehicle("123ABC02"), _vehicle("456DEF02")]),
])
def test_rejected_or_multiple_native_numbers_go_to_gpt_without_using_first_candidate(model, detections):
    row = photographed(model=model)
    with patch.object(ai, "_request", return_value=(200, _payload(model, detections))) as primary, patch.object(identity, "gpt_number", return_value=("", "unknown", "response-test")) as gpt:
        assert identity.process_once(row.pk)
    primary.assert_called_once()
    gpt.assert_called_once_with(JPEG)
    row.refresh_from_db()
    assert row.identity_status == "unidentified"
    assert row.identity_error == "number_unreadable"
    assert row.number == ""
    assert row.session.total_bags == 1


def test_snapshot_and_ocr_have_separate_leases_and_no_network_inside_transaction():
    row = segment()

    def capture(camera):
        assert camera == "cam7"
        assert not connection.in_atomic_block
        assert not identity.capture_once(row.pk)
        assert not identity.process_once(row.pk)
        assert ShippingLoadingSegment.objects.get(pk=row.pk).photo_attempted
        return JPEG

    with patch.object(identity, "capture_frame", side_effect=capture) as frame:
        assert identity.capture_once(row.pk)
    frame.assert_called_once()
    row.refresh_from_db()
    assert row.identity_status == "pending"
    assert row.identity_attempts == 0
    assert not row.primary_attempted


@pytest.mark.parametrize("age,camera,error", [
    (16, "cam7", "photo_window_expired"),
    (0, "", "number_camera_not_configured"),
])
def test_late_replay_or_unconfigured_camera_preserves_count_without_new_live_photo(age, camera, error):
    row = segment(age=age, number_camera=camera)
    with patch.object(identity, "capture_frame") as frame, patch.object(ai, "_request") as primary:
        assert identity.capture_once(row.pk)
        assert not identity.process_once(row.pk)
    frame.assert_not_called()
    primary.assert_not_called()
    row.refresh_from_db()
    assert row.identity_error == error
    assert row.identity_status == "unidentified"
    assert row.total_bags == 1
    assert ShippingLoadingEvent.objects.count() == 1


def test_fresh_photo_is_claimed_before_eighty_expired_segments_and_old_rows_still_finish():
    old_ids = [segment(age=60+index, camera=f"cam{index+10}").pk for index in range(80)]
    fresh = segment()
    with patch.object(identity, "capture_frame", return_value=JPEG) as frame:
        assert identity.capture_once()
        fresh.refresh_from_db()
        assert fresh.photo.read() == JPEG
        assert fresh.identity_status == "pending"
        # Cleanup remains available once there are no fresh unclaimed photos.
        for _ in old_ids:
            assert identity.capture_once()
        assert not identity.capture_once()
    frame.assert_called_once_with("cam7")
    assert ShippingLoadingSegment.objects.filter(
        pk__in=old_ids, identity_status="unidentified", identity_error="photo_window_expired",
    ).count() == 80
    assert ShippingLoadingEvent.objects.count() == 81


def test_snapshot_response_crossing_deadline_cannot_attach_later_transport():
    row = segment()
    clock = {"now": row.started_at}

    def late(camera):
        clock["now"] += timedelta(seconds=16)
        return JPEG

    with patch.object(identity.timezone, "now", side_effect=lambda: clock["now"]), patch.object(identity, "capture_frame", side_effect=late):
        assert identity.capture_once(row.pk)
    row.refresh_from_db()
    assert not row.photo
    assert row.identity_error == "photo_window_expired"
    assert row.total_bags == 1


def test_interrupted_snapshot_is_not_retried_against_new_vehicle():
    row = segment()
    ShippingLoadingSegment.objects.filter(pk=row.pk).update(
        photo_attempted=True, identity_status="processing", identity_lease_until=timezone.now()-timedelta(seconds=1),
    )
    with patch.object(identity, "capture_frame") as frame:
        assert identity.capture_once(row.pk)
    frame.assert_not_called()
    row.refresh_from_db()
    assert row.identity_error == "photo_capture_interrupted"
    assert row.total_bags == 1


def test_camera_failure_is_terminal_without_repeated_snapshot_or_count_loss():
    row = segment()
    with patch.object(identity, "capture_frame", return_value=None) as frame, patch.object(ai, "_request") as primary:
        assert identity.capture_once(row.pk)
        assert not identity.capture_once(row.pk)
        assert not identity.process_once(row.pk)
    frame.assert_called_once()
    primary.assert_not_called()
    row.refresh_from_db()
    assert row.identity_error == "photo_unavailable"
    assert row.total_bags == 1


def test_photo_storage_failure_happens_outside_transaction_and_preserves_counts():
    row = segment()

    def unavailable(*args, **kwargs):
        assert not connection.in_atomic_block
        raise OSError("storage offline")

    with patch.object(identity, "capture_frame", return_value=JPEG), patch.object(row.photo.storage, "save", side_effect=unavailable):
        assert identity.capture_once(row.pk)
    row.refresh_from_db()
    assert not row.photo
    assert row.identity_error == "photo_storage_unavailable"
    assert row.total_bags == 1


def test_primary_request_crash_resumes_with_gpt_without_second_primary():
    row = photographed()
    with patch.object(ai, "_request", side_effect=SystemExit("worker stopped after dispatch")) as primary:
        with pytest.raises(SystemExit):
            identity.process_once(row.pk)
        ShippingLoadingSegment.objects.filter(pk=row.pk).update(identity_lease_until=timezone.now()-timedelta(seconds=1))
        with patch.object(identity, "gpt_number", return_value=("123ABC02", "vehicle_number", "response-test")):
            assert identity.process_once(row.pk)
    primary.assert_called_once()
    row.refresh_from_db()
    assert row.number_source == "gpt"
    assert row.identity_attempts == 2


def test_gpt_retries_are_bounded_and_never_repeat_primary_or_snapshot():
    row = photographed()
    with patch.object(ai, "_request", side_effect=ai.AiUnavailable("offline")) as primary, patch.object(identity, "gpt_number", side_effect=TimeoutError) as gpt, patch.object(identity, "capture_frame") as frame:
        for _ in range(3):
            ShippingLoadingSegment.objects.filter(pk=row.pk).update(identity_next_attempt_at=timezone.now())
            assert identity.process_once(row.pk)
        assert not identity.process_once(row.pk)
    assert primary.call_count == 1
    assert gpt.call_count == 3
    frame.assert_not_called()
    row.refresh_from_db()
    assert row.identity_status == "unidentified"
    assert row.identity_error == "fallback_unavailable"
    assert row.total_bags == 1


def test_unreadable_number_is_terminal_and_does_not_change_bag_ledger():
    row = photographed()
    with patch.object(ai, "_request", return_value=(200, _payload("vehicle_number", [_vehicle(accepted=False)]))), patch.object(identity, "gpt_number", return_value=("", "unknown", "response-test")):
        assert identity.process_once(row.pk)
    row.refresh_from_db()
    assert row.identity_error == "number_unreadable"
    assert row.identity_status == "unidentified"
    assert row.number == ""
    assert row.session.total_bags == 1


def test_operator_correction_during_primary_is_not_overwritten_by_late_ai():
    row = photographed()

    def primary(*args, **kwargs):
        # A stale worker can finish after a replacement lease has already
        # exhausted automatic recovery and exposed the manual exception action.
        ShippingLoadingSegment.objects.filter(pk=row.pk).update(identity_status="unidentified", identity_lease_until=None)
        shipping_segments.apply_identity(row.pk, "456DEF02", "manual")
        return 200, _payload("vehicle_number", [_vehicle()])

    with patch.object(ai, "_request", side_effect=primary):
        assert identity.process_once(row.pk)
    row.refresh_from_db()
    assert (row.number, row.number_source) == ("456DEF02", "manual")
    assert row.total_bags == 1


@pytest.mark.parametrize("configured_model", [None, "wagon_number"])
def test_gpt_request_has_no_candidate_priming_and_strict_schema_for_original_image(configured_model):
    result = {"number": "00123455", "number_clear": True, "recognition_model": "wagon_number"}
    response = Mock(status=200)
    response.read.return_value = json.dumps({"status": "completed", "id": "response-test", "output": [{
        "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(result)}],
    }]}).encode()
    client = Mock()
    client.getresponse.return_value = response
    with patch.object(identity.http.client, "HTTPSConnection", return_value=client):
        assert identity.gpt_number(JPEG, recognition_model=configured_model) == ("00123455", "wagon_number", "response-test")
    body = json.loads(client.request.call_args.kwargs["body"])
    assert body["model"] == "gpt-5-mini"
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True
    image = body["input"][0]["content"][0]["image_url"]
    assert base64.b64decode(image.split(",", 1)[1]) == JPEG
    assert "00123455" not in json.dumps(body)
    if configured_model:
        assert body["text"]["format"]["schema"]["properties"]["recognition_model"]["enum"] == ["wagon_number", "unknown"]
        assert "configured for wagon_number" in body["instructions"]
    assert identity.GPT_SCHEMA["properties"]["recognition_model"]["enum"] == ["vehicle_number", "wagon_number", "unknown"]


def openai_client(payload, *, status=200):
    response = Mock(status=status)
    response.read.return_value = json.dumps(payload).encode()
    client = Mock()
    client.getresponse.return_value = response
    return client


def completed_verdict(number="00123455", *, clear=True, model="wagon_number"):
    return {"status": "completed", "id": "resp_reason_test", "output": [{
        "type": "message", "role": "assistant", "content": [{
            "type": "output_text", "text": json.dumps({
                "number": number, "number_clear": clear, "recognition_model": model,
            }),
        }],
    }]}


@pytest.mark.parametrize("detail", ["high", "original"])
def test_shipping_model_and_detail_override_preserve_original_and_single_request(settings, detail):
    settings.SHIPPING_WAGON_AI_MODEL = "gpt-6-astra"
    settings.SHIPPING_WAGON_AI_DETAIL = detail
    client = openai_client(completed_verdict())
    with patch.object(identity.http.client, "HTTPSConnection", return_value=client):
        assert identity.gpt_number(JPEG, recognition_model="wagon_number")[0] == "00123455"
    assert client.request.call_count == 1
    body = json.loads(client.request.call_args.kwargs["body"])
    assert body["model"] == "gpt-6-astra"
    assert body["reasoning"]["effort"] == "medium"
    assert body["max_output_tokens"] == 3000
    assert "stencil" in body["instructions"]
    assert settings.WEIGHING_AI_MODEL == "gpt-5-mini"
    content = body["input"][0]["content"]
    assert len(content) == 1
    assert content[0]["detail"] == detail
    assert base64.b64decode(content[0]["image_url"].split(",", 1)[1]) == JPEG


@pytest.mark.parametrize("configured_model", [None, "vehicle_number"])
def test_wagon_model_upgrade_does_not_upgrade_truck_fallback(settings, configured_model):
    settings.SHIPPING_WAGON_AI_MODEL = "gpt-6-astra"
    settings.SHIPPING_WAGON_AI_DETAIL = "original"
    client = openai_client(completed_verdict("123ABC02", model="vehicle_number"))
    with patch.object(identity.http.client, "HTTPSConnection", return_value=client):
        assert identity.gpt_number(JPEG, recognition_model=configured_model)[0] == "123ABC02"
    body = json.loads(client.request.call_args.kwargs["body"])
    assert body["model"] == "gpt-5-mini"
    assert body["input"][0]["content"][0]["detail"] == "high"
    assert body["reasoning"]["effort"] == "low"
    assert body["max_output_tokens"] == 1200
    assert "stencil" not in body["instructions"]


@pytest.mark.parametrize("payload,code", [
    (completed_verdict(clear=False), "number_unreadable"),
    (completed_verdict("0012345"), "number_invalid_format"),
    (completed_verdict("00123456"), "wagon_checksum_invalid"),
    (completed_verdict("123ABC02", model="vehicle_number"), "transport_type_mismatch"),
])
def test_completed_refusal_reason_is_preserved_without_retries_or_bag_changes(payload, code, caplog):
    row = photographed(model="wagon_number")
    client = openai_client(payload)
    with patch.object(identity.http.client, "HTTPSConnection", return_value=client), patch.object(ai, "_request") as primary, patch.object(identity, "capture_frame") as frame:
        assert identity.process_once(row.pk)
        assert not identity.process_once(row.pk)
    row.refresh_from_db()
    assert row.identity_error == code
    assert row.identity_status == "unidentified"
    assert row.identity_response_id == "resp_reason_test"
    assert row.identity_attempts == 1
    assert row.identity_next_attempt_at is None
    assert row.number == "" and not row.primary_attempted
    assert row.total_bags == row.session.total_bags == 1
    assert ShippingLoadingEvent.objects.count() == 1
    assert row.photo.read() == JPEG
    client.request.assert_called_once()
    primary.assert_not_called()
    frame.assert_not_called()
    assert code in caplog.text and "resp_reason_test" in caplog.text
    assert "00123456" not in caplog.text
    assert "unit-test-only" not in caplog.text


@pytest.mark.parametrize("status,code,retryable", [
    (401, "openai_authentication_failed", False),
    (403, "openai_authentication_failed", False),
    (400, "openai_request_rejected", False),
    (429, "openai_rate_limited", True),
    (503, "openai_unavailable", True),
])
def test_http_error_retains_safe_reason_and_only_transient_failures_retry(status, code, retryable, caplog):
    row = photographed(model="wagon_number")
    client = openai_client({"private_provider_error": "do-not-log-upstream-body"}, status=status)
    with patch.object(identity.http.client, "HTTPSConnection", return_value=client):
        assert identity.process_once(row.pk)
    row.refresh_from_db()
    assert row.identity_error == code
    assert row.identity_status == ("pending" if retryable else "unidentified")
    assert bool(row.identity_next_attempt_at) is retryable
    assert row.total_bags == 1 and row.number == ""
    assert "do-not-log-upstream-body" not in caplog.text
    client.getresponse.return_value.read.assert_not_called()


@pytest.mark.parametrize("payload,code,retryable", [
    ({"status": "incomplete", "id": "resp_reason_test", "incomplete_details": {"reason": "max_output_tokens"}}, "openai_output_limit", False),
    ({"status": "incomplete", "id": "resp_reason_test", "incomplete_details": {"reason": "content_filter"}}, "openai_refused", False),
    ({"status": "incomplete", "id": "resp_reason_test"}, "openai_incomplete", True),
    ({"status": "completed", "id": "resp_reason_test", "output": []}, "openai_invalid_response", False),
    ({"status": "completed", "id": "resp_reason_test", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "private-response"}]}]}, "openai_refused", False),
])
def test_incomplete_or_malformed_openai_result_preserves_typed_reason(payload, code, retryable):
    with patch.object(identity.http.client, "HTTPSConnection", return_value=openai_client(payload)):
        with pytest.raises(identity.RecognitionFailure) as caught:
            identity.gpt_number(JPEG, recognition_model="wagon_number")
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert caught.value.response_id == "resp_reason_test"
    assert "private-response" not in str(caught.value)


def test_typed_rate_limit_retry_stops_at_existing_limit_on_same_saved_frame():
    row = photographed(model="wagon_number")
    client = openai_client({}, status=429)
    with patch.object(identity.http.client, "HTTPSConnection", return_value=client), patch.object(identity, "capture_frame") as frame, patch.object(ai, "_request") as primary:
        for _ in range(3):
            ShippingLoadingSegment.objects.filter(pk=row.pk).update(identity_next_attempt_at=timezone.now())
            assert identity.process_once(row.pk)
        assert not identity.process_once(row.pk)
    row.refresh_from_db()
    assert row.identity_error == "openai_rate_limited"
    assert row.identity_status == "unidentified"
    assert row.identity_next_attempt_at is None
    assert row.identity_attempts == 3 and row.total_bags == 1
    assert client.request.call_count == 3
    for call in client.request.call_args_list:
        body = json.loads(call.kwargs["body"])
        data_url = body["input"][0]["content"][0]["image_url"]
        assert base64.b64decode(data_url.split(",", 1)[1]) == JPEG
    frame.assert_not_called()
    primary.assert_not_called()


def test_late_wagon_gpt_response_cannot_overwrite_manual_number():
    row = photographed(model="wagon_number")

    def gpt(frame, **kwargs):
        ShippingLoadingSegment.objects.filter(pk=row.pk).update(identity_status="unidentified", identity_lease_until=None)
        shipping_segments.apply_identity(row.pk, "00123463", "manual")
        return "00123455", "wagon_number", "late-response"

    with patch.object(ai, "_request") as primary, patch.object(identity, "gpt_number", side_effect=gpt):
        assert identity.process_once(row.pk)
    primary.assert_not_called()
    row.refresh_from_db()
    assert (row.number, row.number_source) == ("00123463", "manual")
    assert row.total_bags == row.session.total_bags == 1


@pytest.mark.parametrize("value,model,expected", [
    ("00123455", "wagon_number", "00123455"),
    ("00123456", "wagon_number", ""),
    ("1234567", "wagon_number", ""),
    ("KZ 123 ABC 02", "vehicle_number", "123ABC02"),
    ("X123ABC", "vehicle_number", "X123ABC"),
    ("160AL17", "vehicle_number", "160AL17"),
    ("123ABC0O", "vehicle_number", ""),
])
def test_local_number_validation_preserves_characters_and_rejects_bad_wagon_checksum(value, model, expected):
    assert identity.valid_number(value, model) == expected
    assert shipping_segments.normalized_number(value, model) == expected


def test_slow_primary_renews_lease_before_gpt_on_saved_photo():
    row = photographed()
    clock = {"now": timezone.now()}

    def primary(*args, **kwargs):
        clock["now"] += timedelta(seconds=75)
        raise ai.AiUnavailable("slow primary")

    def gpt(frame):
        row.refresh_from_db()
        assert row.identity_lease_until-clock["now"] == timedelta(seconds=90)
        assert frame == JPEG
        return "123ABC02", "vehicle_number", "response-test"

    with patch.object(identity.timezone, "now", side_effect=lambda: clock["now"]), patch.object(ai, "_request", side_effect=primary), patch.object(identity, "gpt_number", side_effect=gpt):
        assert identity.process_once(row.pk)
    row.refresh_from_db()
    assert row.identity_status == "identified"
    assert row.number_source == "gpt"


def test_journal_photo_gpt_idle_resume_merges_same_transport_and_splits_different_transport():
    from apps.cameras.tests.test_shipping_segments import add_events

    start = timezone.now()
    clock = {"now": start}
    ShippingSessionSettings.objects.update_or_create(singleton=True, defaults={
        "activated_at": start-timedelta(seconds=1), "idle_timeout_seconds": 30,
    })
    ShippingTransportCamera.objects.create(conveyor_camera="cam3", number_camera="cam7", recognition_model="vehicle_number")
    photos = [JPEG+b"first", JPEG+b"second", JPEG+b"third"]
    numbers = iter(["123ABC02", "123ABC02", "456DEF02"])
    original_session = None

    def gpt(frame):
        assert frame in photos
        assert not connection.in_atomic_block
        return next(numbers), "vehicle_number", "response-test"

    with patch.object(identity.timezone, "now", side_effect=lambda: clock["now"]), patch.object(identity, "capture_frame", side_effect=photos) as frame, patch.object(ai, "_request", return_value=(200, _payload("vehicle_number", [_vehicle(accepted=False)]))) as primary, patch.object(identity, "gpt_number", side_effect=gpt) as fallback:
        for index, first_second in enumerate((0, 33, 66)):
            clock["now"] = start+timedelta(seconds=first_second+1)
            add_events(start, [first_second, first_second+1], camera="cam3")
            imported = shipping_segments.ingest_camera("cam3")
            assert imported["processed"] == 2
            [segment_id] = imported["created_segment_ids"]
            assert identity.capture_once(segment_id)
            assert identity.process_once(segment_id)
            assert not identity.process_once(segment_id)
            part = ShippingLoadingSegment.objects.get(pk=segment_id)
            assert part.total_bags == 2
            assert part.photo.read() == photos[index]
            if index == 0:
                original_session = part.session_id
            elif index == 1:
                assert part.session_id == original_session
                assert part.session.total_bags == 4
            else:
                assert part.session_id != original_session
                assert part.session.total_bags == 2
            clock["now"] = start+timedelta(seconds=first_second+32)
            AlwaysOnCounterCursor.objects.filter(camera="cam3").update(event_caught_up_at=clock["now"])
            shipping_segments.close_idle("cam3", now=clock["now"])
            part.refresh_from_db()
            assert part.ended_at == part.last_counted_at
    assert frame.call_count == primary.call_count == fallback.call_count == 3
    assert ShippingLoadingEvent.objects.count() == 6
    assert ShippingLoadingSegment.objects.count() == 3
    sessions = ShippingLoadingSession.objects.exclude(status="merged").order_by("started_at")
    assert list(sessions.values_list("number", "total_bags", "status")) == [
        ("123ABC02", 4, "closed"), ("456DEF02", 2, "closed"),
    ]
