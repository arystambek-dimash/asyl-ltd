import hashlib
import uuid

import pytest

from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.conveyors.credentials import digest_token
from apps.conveyors.models import ConveyorDevice
from apps.conveyors.services import arm_session, prepare_session, transport_for
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db

SYNC_URL = "/api/conveyors/v1/device/sync/"
OBSERVATION_URL = "/api/conveyors/v1/ai/observation/"
DEVICE_TOKEN = "A" * 43
BOOT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
EDGE_BOOT_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(autouse=True)
def conveyor_settings(settings):
    settings.CONVEYOR_AI_CALLBACK_TOKEN_SHA256 = hashlib.sha256(
        b"camera-callback-secret"
    ).hexdigest()


def _device(**overrides):
    return ConveyorDevice.objects.create(
        name="ESP32 conveyor",
        camera_source="cam2",
        secret_sha256=digest_token(DEVICE_TOKEN),
        **overrides,
    )


def _credential(device, token=DEVICE_TOKEN):
    return f"Device {device.public_id}.{token}"


def _sync_body(seq=0, **overrides):
    return {
        "protocol_version": 1,
        "boot_id": str(BOOT_ID),
        "seq": seq,
        "ack_revision": None,
        "output_state": 0,
        "feedback_state": 0,
        "fault": None,
        "uptime_ms": seq * 500,
        "wifi_rssi": -61,
        "firmware": "1.0.0",
        **overrides,
    }


def _sync(api_client, device, seq=0, **overrides):
    return api_client.post(
        SYNC_URL,
        _sync_body(seq, **overrides),
        format="json",
        HTTP_AUTHORIZATION=_credential(device),
    )


def _order_session(user, *, status=AiCountingSession.STARTING, target=10):
    client = Client.objects.create_with_user(
        first_name="Cloud", last_name="Conveyor", phone=f"cloud-{target}"
    )
    order = Order.objects.create(client=client, status="confirmed")
    OrderItem.objects.create(order=order, quantity=target)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=status,
        started_by=user,
        target_total=target,
        conveyor_enabled=True,
        conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
    )
    return order, session


def _observation_body(session, seq=0, total=0, terminal_reason=None, **extra):
    return {
        "protocol_version": 1,
        "camera": "cam2",
        "session_id": session.pk,
        "target_total": session.target_total,
        "edge_boot_id": str(EDGE_BOOT_ID),
        "seq": seq,
        "total": total,
        "terminal_reason": terminal_reason,
        **extra,
    }


def _observe(api_client, session, seq=0, total=0, terminal_reason=None, **extra):
    return api_client.post(
        OBSERVATION_URL,
        _observation_body(session, seq, total, terminal_reason, **extra),
        format="json",
        HTTP_AUTHORIZATION="Bearer camera-callback-secret",
    )


def test_sync_requires_per_device_hashed_credential(api_client):
    device = _device()
    assert api_client.post(SYNC_URL, _sync_body(), format="json").status_code == 401
    response = api_client.post(
        SYNC_URL,
        _sync_body(),
        format="json",
        HTTP_AUTHORIZATION=_credential(device, "B" * 43),
    )
    assert response.status_code == 401
    device.refresh_from_db()
    assert device.last_seen_at is None


def test_initial_sync_is_strict_fail_off_and_never_returns_secret(api_client):
    device = _device()
    response = _sync(api_client, device)
    assert response.status_code == 200, response.data
    assert response.data == {
        "protocol_version": 1,
        "server_time": response.data["server_time"],
        "next_sync_ms": 500,
        "command": {
            "revision": 1,
            "state": 0,
            "lease_ms": 0,
            "session_id": None,
            "target_total": None,
            "reason": "enrolled",
        },
    }
    serialized = repr(response.data).lower()
    assert "secret" not in serialized
    assert "token" not in serialized


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("seq", "1"),
        ("seq", True),
        ("output_state", False),
        ("feedback_state", "0"),
        ("protocol_version", "1"),
        ("ack_revision", "1"),
    ],
)
def test_sync_rejects_coerced_scalar_types(api_client, change, value):
    device = _device()
    response = api_client.post(
        SYNC_URL,
        _sync_body(**{change: value}),
        format="json",
        HTTP_AUTHORIZATION=_credential(device),
    )
    assert response.status_code == 400


def test_sync_rejects_unknown_fields_and_noncanonical_uuid(api_client):
    device = _device()
    unknown = _sync(api_client, device, unexpected=True)
    assert unknown.status_code == 400
    uppercase = _sync(
        api_client,
        device,
        boot_id="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
    )
    assert uppercase.status_code == 400


def test_device_sequence_is_strictly_increasing(api_client):
    device = _device()
    assert _sync(api_client, device, 5).status_code == 200
    assert _sync(api_client, device, 5).status_code == 409
    assert _sync(api_client, device, 4).status_code == 409
    assert _sync(api_client, device, 6).status_code == 200


def test_device_fault_gets_new_off_fence_and_clears_after_safe_ack(api_client):
    device = _device(
        desired_state=True,
        command_revision=5,
        command_terminal=False,
        stop_reason="active_session",
    )
    faulted = _sync(
        api_client,
        device,
        0,
        ack_revision=5,
        fault="lease_expired",
    )
    assert faulted.status_code == 200, faulted.data
    assert faulted.data["command"]["state"] == 0
    assert faulted.data["command"]["revision"] == 6

    safe_ack = _sync(api_client, device, 1, ack_revision=6)
    assert safe_ack.status_code == 200, safe_ack.data
    assert safe_ack.data["command"]["state"] == 0
    assert safe_ack.data["command"]["revision"] == 6
    device.refresh_from_db()
    assert device.fault == ""
    assert device.command_terminal is True
    assert device.stop_reason == "device_fault"


def test_cloud_session_gets_on_lease_only_after_off_and_fresh_ai(
    api_client, make_user,
):
    device = _device()
    user = make_user("cloud-loader")
    order, session = _order_session(user)
    assert _sync(api_client, device, 0).status_code == 200
    prepared = prepare_session(session)
    response = _sync(
        api_client,
        device,
        1,
        ack_revision=prepared.command_revision,
    )
    assert response.data["command"]["state"] == 0
    assert response.data["command"]["session_id"] is None

    observation = _observe(api_client, session, total=0)
    assert observation.status_code == 200, observation.data
    session.status = AiCountingSession.ACTIVE
    session.save(update_fields=["status"])
    order.status = "loading"
    order.loading_camera = "cam2"
    order.save(update_fields=["status", "loading_camera"])
    armed = arm_session(session)
    assert armed.desired_state is True

    command = _sync(
        api_client,
        device,
        2,
        ack_revision=prepared.command_revision,
    )
    assert command.status_code == 200, command.data
    assert command.data["command"] == {
        "revision": armed.command_revision,
        "state": 1,
        "lease_ms": 1200,
        "session_id": session.pk,
        "target_total": 10,
        "reason": "active_session",
    }

    confirmed = _sync(
        api_client,
        device,
        3,
        ack_revision=armed.command_revision,
        output_state=1,
        feedback_state=1,
    )
    assert confirmed.data["command"]["state"] == 1


def test_open_session_uses_its_frozen_cloud_transport(make_user):
    _device()
    _order, session = _order_session(make_user("frozen-cloud-loader"))

    prepared = prepare_session(session)

    assert prepared.command_session_id == session.pk
    assert prepared.command_target_total == session.target_total
    assert prepared.desired_state is False
    assert prepared.command_terminal is False


def test_target_observation_terminally_stops_and_off_has_null_binding(
    api_client, make_user,
):
    device = _device()
    order, session = _order_session(make_user("target-loader"))
    _sync(api_client, device, 0)
    prepared = prepare_session(session)
    _sync(api_client, device, 1, ack_revision=prepared.command_revision)
    _observe(api_client, session, 0, 0)
    session.status = AiCountingSession.ACTIVE
    session.save(update_fields=["status"])
    order.status = "loading"
    order.loading_camera = "cam2"
    order.save(update_fields=["status", "loading_camera"])
    armed = arm_session(session)
    _sync(
        api_client, device, 2,
        ack_revision=armed.command_revision,
        output_state=1, feedback_state=1,
    )

    stopped = _observe(
        api_client, session, 1, 10, terminal_reason="target_reached",
    )
    assert stopped.status_code == 200, stopped.data
    assert stopped.data["desired_state"] == 0
    assert stopped.data["terminal"] is True
    assert stopped.data["reason"] == "target_reached"
    off = _sync(
        api_client, device, 3,
        ack_revision=armed.command_revision,
        output_state=1, feedback_state=1,
    )
    assert off.data["command"]["state"] == 0
    assert off.data["command"]["lease_ms"] == 0
    assert off.data["command"]["session_id"] is None
    assert off.data["command"]["target_total"] is None


def test_exact_target_retry_is_idempotent_but_changed_payload_conflicts(
    api_client, make_user,
):
    _device()
    _order, session = _order_session(make_user("retry-loader"))
    prepare_session(session)
    first = _observe(
        api_client, session, 8, 10, terminal_reason="target_reached",
    )
    retry = _observe(
        api_client, session, 8, 10, terminal_reason="target_reached",
    )
    conflict = _observe(api_client, session, 8, 10, terminal_reason=None)
    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.data["duplicate"] is True
    assert conflict.status_code == 409


def test_regression_terminal_retry_keeps_exact_idempotency(api_client, make_user):
    device = _device()
    _order, session = _order_session(make_user("regression-retry-loader"))
    prepared = prepare_session(session)
    assert _observe(api_client, session, 1, 5).status_code == 200

    regressed = _observe(
        api_client, session, 2, 4, terminal_reason="counter_regressed",
    )
    retry = _observe(
        api_client, session, 2, 4, terminal_reason="counter_regressed",
    )
    conflict = _observe(
        api_client, session, 2, 5, terminal_reason="counter_regressed",
    )

    assert regressed.status_code == 200
    assert regressed.data["reason"] == "counter_regressed"
    device.refresh_from_db()
    assert device.command_revision == prepared.command_revision + 1
    assert retry.status_code == 200
    assert retry.data["duplicate"] is True
    assert conflict.status_code == 409


def test_new_device_boot_never_auto_resumes_on(api_client, make_user):
    device = _device()
    order, session = _order_session(make_user("boot-loader"))
    _sync(api_client, device, 0)
    prepared = prepare_session(session)
    _sync(api_client, device, 1, ack_revision=prepared.command_revision)
    _observe(api_client, session)
    session.status = AiCountingSession.ACTIVE
    session.save(update_fields=["status"])
    order.status = "loading"
    order.loading_camera = "cam2"
    order.save(update_fields=["status", "loading_camera"])
    armed = arm_session(session)

    reboot = _sync(
        api_client,
        device,
        0,
        boot_id="33333333-3333-4333-8333-333333333333",
        ack_revision=armed.command_revision,
    )
    assert reboot.status_code == 200
    assert reboot.data["command"]["state"] == 0
    assert reboot.data["command"]["revision"] > armed.command_revision
    device.refresh_from_db()
    assert device.command_terminal is True
    assert device.stop_reason == "device_reboot"


def test_admin_enroll_shows_secret_once_and_rotate_revokes_old(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="conveyor-root", password="pass12345",
    )
    client = auth_client(superuser)
    enrolled = client.post(
        "/api/conveyors/devices/",
        {"name": "Belt ESP", "camera_source": "cam2"},
        format="json",
    )
    assert enrolled.status_code == 201, enrolled.data
    credential = enrolled.data["credential"]
    assert credential["token"]
    public_id = enrolled.data["public_id"]

    listing = client.get("/api/conveyors/devices/")
    detail = client.get(f"/api/conveyors/devices/{public_id}/")
    assert "credential" not in listing.data[0]
    assert "credential" not in detail.data
    assert "secret_sha256" not in repr(listing.data)

    rotated = client.post(
        f"/api/conveyors/devices/{public_id}/rotate-secret/",
        {},
        format="json",
    )
    assert rotated.status_code == 200
    assert rotated.data["credential"]["token"] != credential["token"]
    old = api_client.post(
        SYNC_URL,
        _sync_body(),
        format="json",
        HTTP_AUTHORIZATION=credential["authorization"],
    )
    assert old.status_code == 401


def test_admin_emergency_and_disable_are_off_only(
    auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="stop-root", password="pass12345",
    )
    device = _device(
        desired_state=True,
        command_terminal=False,
        stop_reason="active_session",
    )
    client = auth_client(superuser)
    emergency = client.post(
        f"/api/conveyors/devices/{device.public_id}/emergency-stop/",
        {}, format="json",
    )
    assert emergency.status_code == 200
    assert emergency.data["desired_state"] == 0
    assert emergency.data["command_terminal"] is True
    revision = emergency.data["command_revision"]

    disabled = client.post(
        f"/api/conveyors/devices/{device.public_id}/disable/",
        {}, format="json",
    )
    assert disabled.status_code == 200
    assert disabled.data["is_active"] is False
    assert disabled.data["command_revision"] > revision


def test_device_binding_itself_selects_server_managed_transport(
    auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="direct-root", password="pass12345",
    )
    assert transport_for("cam2") == AiCountingSession.CONVEYOR_DIRECT

    response = auth_client(superuser).post(
        "/api/conveyors/devices/",
        {"name": "Camera-owned ESP32", "camera_source": "cam2"},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert transport_for("cam2") == AiCountingSession.CONVEYOR_CLOUD

    device = ConveyorDevice.objects.get(camera_source="cam2")
    device.is_active = False
    device.save(update_fields=["is_active"])
    assert transport_for("cam2") == AiCountingSession.CONVEYOR_DIRECT
