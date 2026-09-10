from concurrent.futures import ThreadPoolExecutor
from http.client import IncompleteRead
import io
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.db import close_old_connections
from rest_framework.test import APIClient
from PIL import Image

from apps.cameras import ai, services, shipping_segment_identity as identity, transport_recognition
from apps.cameras.api_views.access import CAM_COOKIE
from apps.cameras.models import (
    AiCountingSession,
    MonoblockCameraSettings,
    ShippingLoadingEvent,
    ShippingLoadingSegment,
    ShippingTransportCamera,
)
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order

pytestmark = pytest.mark.django_db
URL = "/api/cameras/cam2/transport-camera/"
DATA = {"number_camera": "cam7", "recognition_model": "vehicle_number"}
FRAME = b"\xff\xd8\xff\xe0one shipping camera frame"


@pytest.fixture
def setup(monkeypatch, django_user_model, settings):
    root = django_user_model.objects.create_superuser(
        username="transport-root", password="test"
    )
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam2", "cam3"],
        always_on_camera_sources=["cam4"],
        wagon_number_camera_source="cam9",
    )
    monkeypatch.setattr(ai, "AI_KEY", "")
    settings.OPENAI_API_KEY = "unit-test-only"
    monkeypatch.setattr(identity, "capture_frame", Mock(return_value=FRAME))
    monkeypatch.setattr(identity, "primary_number", Mock(return_value="123ABC02"))
    monkeypatch.setattr(identity, "gpt_number", Mock(return_value=("", "unknown", "test-response")))
    monkeypatch.setattr(transport_recognition, "recognize_transport_number", Mock(side_effect=AssertionError("Legacy OCR must not run in shipping check")))
    monkeypatch.setattr(
        services,
        "discover_cameras",
        lambda: [
            {"src": f"cam{number}", "online": False} for number in (2, 3, 7, 8, 9)
        ],
    )
    return root


@pytest.fixture
def binding(setup):
    return ShippingTransportCamera.objects.create(
        conveyor_camera="cam2", **DATA, updated_by=setup
    )


@pytest.mark.parametrize("role", ["boss", "operator", "client_user"])
@pytest.mark.parametrize("method", ["get", "put", "delete", "recognize"])
def test_configuration_is_superuser_only(
    setup, auth_client, request, role, method, monkeypatch
):
    discovery = Mock()
    recognize = Mock()
    monkeypatch.setattr(services, "discover_cameras", discovery)
    monkeypatch.setattr(transport_recognition, "recognize_transport_number", recognize)
    client = auth_client(request.getfixturevalue(role))
    response = (
        client.post(URL + "recognize/")
        if method == "recognize"
        else getattr(client, method)(URL, DATA, format="json")
    )
    assert response.status_code == 403
    assert not ShippingTransportCamera.objects.exists()
    discovery.assert_not_called()
    recognize.assert_not_called()
    identity.capture_frame.assert_not_called()
    identity.primary_number.assert_not_called()
    identity.gpt_number.assert_not_called()


def test_anonymous_cannot_read_configuration(setup, api_client):
    assert api_client.get(URL).status_code == 401


def test_get_unset_is_read_only(setup, auth_client, monkeypatch):
    discovery = Mock()
    monkeypatch.setattr(services, "discover_cameras", discovery)
    response = auth_client(setup).get(URL)
    assert response.status_code == 200
    assert response.data == {
        "conveyor_camera": "cam2",
        "number_camera": None,
        "recognition_model": None,
        "loading_zone": None,
        "updated_at": None,
    }
    assert not ShippingTransportCamera.objects.exists()
    discovery.assert_not_called()


def test_loading_zone_roundtrip_preserved_when_omitted_and_cleared_explicitly(setup, auth_client):
    client = auth_client(setup)
    zone = [0.1, 0.2, 0.8, 0.9]
    saved = client.put(URL, {**DATA, "loading_zone": zone}, format="json")
    assert saved.status_code == 200
    assert saved.data["loading_zone"] == zone
    assert client.put(URL, DATA, format="json").data["loading_zone"] == zone
    assert client.put(URL, {**DATA, "loading_zone": None}, format="json").data["loading_zone"] is None


def test_old_camera_zone_is_not_inherited_when_changing_camera(setup, auth_client):
    client = auth_client(setup)
    client.put(URL, {**DATA, "loading_zone": [0.1, 0.2, 0.8, 0.9]}, format="json")
    result = client.put(URL, {**DATA, "number_camera": "cam8"}, format="json")
    assert result.status_code == 200
    assert result.data["loading_zone"] is None


@pytest.mark.parametrize("zone", [[0, 0, 0, 1], [0.8, 0, 0.1, 1], [-0.1, 0, 1, 1], [0, 0, 1, 1.2], [False, 0, 1, 1], [0, 1], "0,0,1,1"])
def test_invalid_loading_zone_is_rejected(setup, auth_client, zone):
    assert auth_client(setup).put(URL, {**DATA, "loading_zone": zone}, format="json").status_code == 400


def test_zone_cannot_change_under_active_loading(binding, setup, auth_client):
    client = Client.objects.create_with_user(first_name="Zone", phone="zone-guard")
    order = Order.objects.create(client=client, status="loading", loading_camera="cam2")
    AiCountingSession.objects.create(order=order, camera="cam2", status=AiCountingSession.ACTIVE)
    response = auth_client(setup).put(URL, {**DATA, "loading_zone": [0.1, 0.1, 0.8, 0.8]}, format="json")
    assert response.status_code == 400
    binding.refresh_from_db()
    assert binding.loading_zone is None


@pytest.mark.parametrize("model", ["vehicle_number", "wagon_number"])
def test_save_read_and_idempotent_retry(setup, auth_client, model):
    client = auth_client(setup)
    response = client.put(URL, {**DATA, "recognition_model": model}, format="json")
    assert response.status_code == 200, response.data
    saved = ShippingTransportCamera.objects.get()
    assert saved.updated_by == setup
    assert (saved.number_camera, saved.recognition_model) == ("cam7", model)
    retried = client.put(URL, {**DATA, "recognition_model": model}, format="json")
    assert retried.status_code == 200
    assert retried.data["updated_at"] == response.data["updated_at"]
    assert client.get(URL).data == response.data
    assert EventLog.objects.filter(event_type="camera_settings").count() == 1
    settings = MonoblockCameraSettings.objects.get()
    assert settings.wagon_number_camera_source == "cam9"
    assert settings.always_on_camera_sources == ["cam4"]
    assert not AiCountingSession.objects.exists()
    assert not Order.objects.exists()


@pytest.mark.parametrize(
    "data,code",
    [
        ({**DATA, "number_camera": "cam2"}, "same_camera"),
        ({**DATA, "number_camera": "cam99"}, "unknown_number_camera"),
        ({**DATA, "number_camera": 7}, None),
        ({**DATA, "number_camera": "https://example.com/frame"}, None),
        ({**DATA, "number_camera": "cam7main"}, None),
        ({**DATA, "number_camera": "cam" + "1" * 30}, None),
        ({**DATA, "recognition_model": "unknown"}, None),
        ({"number_camera": "cam7"}, None),
        ({"recognition_model": "wagon_number"}, None),
    ],
)
def test_invalid_configuration_is_not_saved(setup, auth_client, data, code):
    response = auth_client(setup).put(URL, data, format="json")
    assert response.status_code == 400
    if code:
        assert response.data["code"] == code
    assert not ShippingTransportCamera.objects.exists()


def test_only_shipping_conveyor_can_be_configured(setup, auth_client):
    response = auth_client(setup).put(
        "/api/cameras/cam4/transport-camera/", DATA, format="json"
    )
    assert response.status_code == 404


def test_unavailable_inventory_does_not_erase_binding(
    setup, binding, auth_client, monkeypatch
):
    monkeypatch.setattr(services, "discover_cameras", lambda: [])
    response = auth_client(setup).put(
        URL, {**DATA, "number_camera": "cam8"}, format="json"
    )
    assert response.status_code == 503
    binding.refresh_from_db()
    assert binding.number_camera == "cam7"
    assert auth_client(setup).put(URL, DATA, format="json").status_code == 200


def test_one_number_camera_cannot_own_two_conveyors(setup, binding, auth_client):
    response = auth_client(setup).put(
        "/api/cameras/cam3/transport-camera/", DATA, format="json"
    )
    assert response.status_code == 409
    assert response.data["code"] == "number_camera_in_use"
    assert ShippingTransportCamera.objects.count() == 1


@pytest.mark.parametrize("owner", ["starting", "active", "legacy_order"])
def test_busy_conveyor_rejects_change_and_delete(setup, binding, auth_client, owner):
    customer = Client.objects.create_with_user(first_name="Transport", phone="loading")
    order = Order.objects.create(
        client=customer, status="loading", loading_camera="cam2"
    )
    if owner != "legacy_order":
        order.loading_camera = ""
        order.save(update_fields=["loading_camera"])
        AiCountingSession.objects.create(order=order, camera="cam2", status=owner)
    client = auth_client(setup)
    for response in (
        client.put(URL, {**DATA, "recognition_model": "wagon_number"}, format="json"),
        client.delete(URL),
    ):
        assert response.status_code == 400
        assert response.data["code"] == "monoblock_busy"
    assert client.put(URL, DATA, format="json").status_code == 200
    binding.refresh_from_db()
    assert binding.recognition_model == "vehicle_number"


def test_delete_is_idempotent_and_releases_camera(setup, binding, auth_client):
    client = auth_client(setup)
    assert client.delete(URL).status_code == 204
    assert client.delete(URL).status_code == 204
    assert client.get(URL).data["number_camera"] is None
    assert (
        client.put(
            "/api/cameras/cam3/transport-camera/", DATA, format="json"
        ).status_code
        == 200
    )
    assert EventLog.objects.filter(event_type="camera_settings").count() == 2


@pytest.mark.parametrize("number,model,expected", [
    ("00123455", "wagon_number", "00123455"),
    ("", "wagon_number", None),
    ("00123456", "wagon_number", None),
    ("123ABC02", "vehicle_number", None),
])
def test_wagon_recognize_uses_only_openai_and_saved_camera_without_accounting(
    setup,
    binding,
    auth_client,
    number,
    model,
    expected,
):
    binding.recognition_model = "wagon_number"
    binding.save()
    identity.gpt_number.return_value = (number, model, "test-response")
    response = auth_client(setup).post(
        URL + "recognize/", {"number_camera": "cam9"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["number"] == expected
    assert response.data["observed_at"]
    identity.capture_frame.assert_called_once_with("cam7")
    identity.gpt_number.assert_called_once_with(FRAME, recognition_model="wagon_number")
    identity.primary_number.assert_not_called()
    transport_recognition.recognize_transport_number.assert_not_called()
    assert not AiCountingSession.objects.exists()
    assert not Order.objects.exists()
    assert not EventLog.objects.exists()
    assert not ShippingLoadingSegment.objects.exists()
    assert not ShippingLoadingEvent.objects.exists()


def test_truck_recognize_uses_primary_without_openai_when_number_is_accepted(setup, binding, auth_client, settings):
    settings.OPENAI_API_KEY = ""
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 200
    assert response.data["number"] == "123ABC02"
    identity.primary_number.assert_called_once_with(FRAME, "vehicle_number")
    identity.gpt_number.assert_not_called()


@pytest.mark.parametrize("primary", [None, ai.AiUnavailable("private camera host"), ai.AiError(503, "private model path")])
def test_truck_recognize_falls_back_on_same_frame_for_missing_or_failed_primary(setup, binding, auth_client, primary):
    if isinstance(primary, Exception):
        identity.primary_number.side_effect = primary
    else:
        identity.primary_number.return_value = primary
    identity.gpt_number.return_value = ("456DEF02", "vehicle_number", "test-response")
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 200
    assert response.data["number"] == "456DEF02"
    identity.capture_frame.assert_called_once_with("cam7")
    identity.gpt_number.assert_called_once_with(FRAME, recognition_model="vehicle_number")


@pytest.mark.parametrize("model", ["vehicle_number", "wagon_number"])
def test_recognize_uses_saved_zone_crop_for_primary_and_openai(setup, binding, auth_client, model):
    image = Image.new("RGB", (120, 80), "red")
    image.paste("blue", (60, 0, 120, 80))
    source = io.BytesIO()
    image.save(source, format="JPEG", quality=95, subsampling=0)
    identity.capture_frame.return_value = source.getvalue()
    identity.primary_number.return_value = None
    binding.recognition_model = model
    binding.loading_zone = [0.5, 0.25, 1, 0.75]
    binding.save()
    response = auth_client(setup).post(URL + "recognize/", {"loading_zone": [0, 0, 0.5, 1]}, format="json")
    assert response.status_code == 200
    crop = identity.gpt_number.call_args.args[0]
    with Image.open(io.BytesIO(crop)) as result:
        assert result.size == (60, 40)
        red, green, blue = result.getpixel((30, 20))
        assert blue > 240 and red < 15 and green < 15
    if model == "vehicle_number":
        identity.primary_number.assert_called_once_with(crop, model)
    else:
        identity.primary_number.assert_not_called()
    assert response.data["loading_zone"] == [0.5, 0.25, 1, 0.75]


@pytest.mark.parametrize(
    "error,expected",
    [
        (ai.AiUnavailable("private host"), 502),
        (ai.AiProtocolError("private payload"), 502),
        (ai.AiError(503, "private model path"), 503),
        (ai.AiError(401, "private token"), 502),
    ],
)
def test_recognize_errors_are_not_unrecognized_numbers(
    setup, binding, auth_client, monkeypatch, error, expected
):
    monkeypatch.setattr(
        identity, "capture_frame", Mock(side_effect=error)
    )
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == expected
    assert "number" not in response.data
    assert "private" not in str(response.data)


@pytest.mark.parametrize("error", [TimeoutError("private key"), IncompleteRead(b"private body"), ValueError("private OpenAI response")])
def test_openai_failure_is_sanitized_and_never_falls_back_to_wagon_native(setup, binding, auth_client, error):
    binding.recognition_model = "wagon_number"
    binding.save()
    identity.gpt_number.side_effect = error
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 502
    assert "number" not in response.data
    assert "private" not in str(response.data)
    identity.primary_number.assert_not_called()


@pytest.mark.parametrize("code", ["number_unreadable", "number_invalid_format", "wagon_checksum_invalid", "transport_type_mismatch"])
def test_manual_check_exposes_semantic_refusal_without_accounting_changes(setup, binding, auth_client, code):
    binding.recognition_model = "wagon_number"
    binding.save()
    identity.gpt_number.side_effect = identity.NumberRejected(code, response_id="resp_manual_test")
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 200
    assert response.data["number"] is None
    assert response.data["identity_error"] == code
    identity.primary_number.assert_not_called()
    assert not ShippingLoadingEvent.objects.exists()
    assert not ShippingLoadingSegment.objects.exists()
    assert not Order.objects.exists()
    assert not EventLog.objects.exists()


@pytest.mark.parametrize("code,retryable,status", [
    ("openai_authentication_failed", False, 503),
    ("openai_rate_limited", True, 503),
    ("openai_output_limit", False, 502),
    ("openai_invalid_response", False, 502),
])
def test_manual_check_preserves_typed_service_error_code(setup, binding, auth_client, code, retryable, status):
    binding.recognition_model = "wagon_number"
    binding.save()
    identity.gpt_number.side_effect = identity.RecognitionFailure(code, retryable=retryable)
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == status
    assert response.data["code"] == code
    assert "number" not in response.data


def test_semantic_refusal_rechecks_camera_binding_before_returning(setup, binding, auth_client):
    binding.recognition_model = "wagon_number"
    binding.save()

    def rejected(*args, **kwargs):
        binding.number_camera = "cam8"
        binding.save()
        raise identity.NumberRejected("number_unreadable")

    identity.gpt_number.side_effect = rejected
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 409
    assert response.data["code"] == "transport_camera_changed"


@pytest.mark.parametrize("model", ["vehicle_number", "wagon_number"])
def test_missing_openai_key_is_service_unavailable_when_needed(setup, binding, auth_client, settings, model):
    binding.recognition_model = model
    binding.save()
    settings.OPENAI_API_KEY = ""
    identity.primary_number.return_value = None
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 503
    assert "number" not in response.data
    identity.gpt_number.assert_not_called()


def test_missing_photo_never_calls_number_models(setup, binding, auth_client):
    identity.capture_frame.return_value = None
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 502
    identity.primary_number.assert_not_called()
    identity.gpt_number.assert_not_called()


def test_recognize_requires_saved_configuration(setup, auth_client):
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 409


def test_recognize_discards_result_after_configuration_changed(
    setup, binding, auth_client, monkeypatch
):
    def recognize(*_):
        binding.recognition_model = "wagon_number"
        binding.save()
        return "123ABC02"

    monkeypatch.setattr(identity, "primary_number", recognize)
    response = auth_client(setup).post(URL + "recognize/")
    assert response.status_code == 409
    assert response.data["code"] == "transport_camera_changed"
    assert "number" not in response.data


def test_removing_conveyor_releases_number_binding(setup, binding, auth_client):
    response = auth_client(setup).put(
        "/api/cameras/monoblock-settings/", {"camera_sources": ["cam3"]}, format="json"
    )
    assert response.status_code == 202, response.data
    assert not ShippingTransportCamera.objects.exists()
    assert EventLog.objects.filter(event_type="camera_settings").count() == 1


def test_non_superuser_cannot_remove_binding_through_conveyor_settings(
    setup, binding, auth_client, boss
):
    response = auth_client(boss).put(
        "/api/cameras/monoblock-settings/", {"camera_sources": ["cam3"]}, format="json"
    )
    assert response.status_code == 403
    assert ShippingTransportCamera.objects.filter(pk=binding.pk).exists()
    assert MonoblockCameraSettings.shipping_sources() == ["cam2", "cam3"]
    assert not EventLog.objects.exists()


@pytest.mark.parametrize(
    "role,expected", [("superuser", 204), ("boss", 403), ("operator", 403)]
)
def test_number_main_stream_preview_requires_superuser(
    setup, auth_client, request, api_client, role, expected, settings
):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = False
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    user = setup if role == "superuser" else request.getfixturevalue(role)
    token = auth_client(user).post("/api/cameras/token/").cookies[CAM_COOKIE].value
    api_client.cookies[CAM_COOKIE] = token
    response = api_client.get(
        "/api/cameras/auth/", HTTP_X_ORIGINAL_URI="/go2rtc/api/ws?src=cam7main"
    )
    assert response.status_code == expected


@pytest.mark.parametrize(
    "source",
    ["https://example.com/feed", "cam7main/../config", "cam7main&src=cam2", "cam0main"],
)
def test_main_preview_does_not_allow_arbitrary_stream_sources(
    setup, auth_client, api_client, source
):
    token = auth_client(setup).post("/api/cameras/token/").cookies[CAM_COOKIE].value
    api_client.cookies[CAM_COOKIE] = token
    response = api_client.get(
        "/api/cameras/auth/", HTTP_X_ORIGINAL_URI=f"/go2rtc/api/ws?src={source}"
    )
    assert response.status_code == 403


def test_save_rechecks_conveyor_after_inventory_read(setup, auth_client, monkeypatch):
    def discover():
        MonoblockCameraSettings.objects.update(camera_sources=["cam3"])
        return [{"src": "cam7"}]

    monkeypatch.setattr(services, "discover_cameras", discover)
    assert auth_client(setup).put(URL, DATA, format="json").status_code == 404
    assert not ShippingTransportCamera.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_assignment_keeps_one_owner(setup, monkeypatch):
    gate = Barrier(2)

    def discover():
        gate.wait(timeout=10)
        return [{"src": "cam7"}]

    monkeypatch.setattr(services, "discover_cameras", discover)

    def assign(camera):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(setup)
            return client.put(
                f"/api/cameras/{camera}/transport-camera/", DATA, format="json"
            ).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(assign, ["cam2", "cam3"]))
    assert sorted(responses) == [200, 409]
    assert ShippingTransportCamera.objects.count() == 1
