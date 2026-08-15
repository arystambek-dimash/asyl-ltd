import hashlib
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.conveyors.credentials import digest_token
from apps.conveyors.models import ConveyorDevice
from apps.conveyors.services import (
    ConveyorDeviceError,
    arm_session,
    emergency_stop,
    prepare_session,
    sync_device,
    transport_for,
)
from apps.eventlog.models import EventLog
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


def _ready_bench_device(api_client):
    device = _device()
    first = _sync(
        api_client,
        device,
        seq=0,
        firmware="1.0.0-bench-d15",
    )
    assert first.status_code == 200
    revision = first.data["command"]["revision"]
    acknowledged = _sync(
        api_client,
        device,
        seq=1,
        ack_revision=revision,
        firmware="1.0.0-bench-d15",
    )
    assert acknowledged.status_code == 200
    device.refresh_from_db()
    return device


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

    first_sync = api_client.post(
        SYNC_URL,
        _sync_body(),
        format="json",
        HTTP_AUTHORIZATION=credential["authorization"],
    )
    assert first_sync.status_code == 200
    off_revision = first_sync.data["command"]["revision"]
    off_ack = api_client.post(
        SYNC_URL,
        _sync_body(seq=1, ack_revision=off_revision),
        format="json",
        HTTP_AUTHORIZATION=credential["authorization"],
    )
    assert off_ack.status_code == 200

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


def test_admin_can_rotate_never_seen_credential_without_provisioning_it(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="unprovisioned-rotate-root", password="pass12345",
    )
    client = auth_client(superuser)
    enrolled = client.post(
        "/api/conveyors/devices/",
        {"name": "Fresh ESP", "camera_source": "cam2"},
        format="json",
    )
    old_credential = enrolled.data["credential"]

    rotated = client.post(
        f"/api/conveyors/devices/{enrolled.data['public_id']}/rotate-secret/",
        {},
        format="json",
    )

    assert rotated.status_code == 200
    assert rotated.data["credential"]["token"] != old_credential["token"]
    rejected = api_client.post(
        SYNC_URL,
        _sync_body(),
        format="json",
        HTTP_AUTHORIZATION=old_credential["authorization"],
    )
    assert rejected.status_code == 401


def test_admin_can_rotate_never_armed_stale_off_credential(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="stale-never-armed-rotate-root", password="pass12345",
    )
    device = _device(
        command_revision=3,
        stop_reason="credential_rotation_pending",
        last_boot_id=BOOT_ID,
        last_sequence=2,
        last_ack_revision=2,
        last_seen_at=timezone.now() - timedelta(minutes=10),
        output_state=False,
        feedback_state=False,
    )
    old_digest = device.secret_sha256

    rotated = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/rotate-secret/",
        {},
        format="json",
    )

    assert rotated.status_code == 200
    device.refresh_from_db()
    assert device.secret_sha256 != old_digest
    event = EventLog.objects.get(event_type="conveyor_device_secret_rotated")
    assert event.payload["rotation_basis"] == "never_armed"
    assert event.payload["previous_revision"] == 3
    assert event.payload["previous_ack_revision"] == 2
    assert event.payload["output_state"] is False
    assert event.payload["feedback_state"] is False
    assert "token" not in event.payload
    assert "secret" not in event.payload
    rejected = _sync(api_client, device, seq=3)
    assert rejected.status_code == 401


def test_rotation_rolls_back_if_audit_write_fails(
    auth_client, django_user_model, monkeypatch,
):
    superuser = django_user_model.objects.create_superuser(
        username="rotation-audit-failure-root", password="pass12345",
    )
    device = _device()
    old_digest = device.secret_sha256
    old_revision = device.command_revision

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("apps.conveyors.views.log_event", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        auth_client(superuser).post(
            f"/api/conveyors/devices/{device.public_id}/rotate-secret/",
            {},
            format="json",
        )

    device.refresh_from_db()
    assert device.secret_sha256 == old_digest
    assert device.command_revision == old_revision


def test_stale_off_with_historical_session_still_requires_fresh_proof(
    auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="historical-session-rotate-root", password="pass12345",
    )
    _, session = _order_session(superuser, status=AiCountingSession.CLOSED)
    device = _device(
        command_session=session,
        command_target_total=session.target_total,
        last_seen_at=timezone.now() - timedelta(minutes=10),
        last_boot_id=BOOT_ID,
        last_sequence=2,
        last_ack_revision=1,
        output_state=False,
        feedback_state=False,
    )
    old_digest = device.secret_sha256

    response = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/rotate-secret/",
        {},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "off_not_confirmed"
    device.refresh_from_db()
    assert device.secret_sha256 == old_digest


@pytest.mark.parametrize(
    "unsafe_history",
    [
        {"armed_device_boot_id": BOOT_ID},
        {"last_total": 1},
        {"fault": "feedback_mismatch"},
    ],
)
def test_never_armed_bypass_rejects_unsafe_history(
    auth_client, django_user_model, unsafe_history,
):
    superuser = django_user_model.objects.create_superuser(
        username=f"unsafe-history-{next(iter(unsafe_history))}",
        password="pass12345",
    )
    device = _device(
        last_seen_at=timezone.now() - timedelta(minutes=10),
        last_boot_id=BOOT_ID,
        last_sequence=2,
        last_ack_revision=1,
        output_state=False,
        feedback_state=False,
        **unsafe_history,
    )

    response = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/rotate-secret/",
        {},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "off_not_confirmed"


def test_admin_cannot_enroll_active_device_on_open_direct_session(
    auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="enroll-open-root", password="pass12345",
    )
    _, session = _order_session(superuser)
    session.conveyor_transport = AiCountingSession.CONVEYOR_DIRECT
    session.save(update_fields=["conveyor_transport"])

    response = auth_client(superuser).post(
        "/api/conveyors/devices/",
        {"name": "Late ESP32", "camera_source": "cam2", "is_active": True},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "device_busy"
    assert not ConveyorDevice.objects.filter(camera_source="cam2").exists()


def test_admin_cannot_activate_device_on_open_direct_session(
    auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="activate-open-root", password="pass12345",
    )
    device = _device(is_active=False)
    _, session = _order_session(superuser)
    session.conveyor_transport = AiCountingSession.CONVEYOR_DIRECT
    session.save(update_fields=["conveyor_transport"])

    response = auth_client(superuser).patch(
        f"/api/conveyors/devices/{device.public_id}/",
        {"is_active": True},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "device_busy"
    device.refresh_from_db()
    assert device.is_active is False


def test_admin_emergency_and_disable_are_off_only(
    api_client, auth_client, django_user_model,
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

    acknowledged = _sync(
        api_client,
        device,
        seq=0,
        ack_revision=revision,
        output_state=0,
        feedback_state=0,
    )
    assert acknowledged.status_code == 200

    disabled = client.post(
        f"/api/conveyors/devices/{device.public_id}/disable/",
        {}, format="json",
    )
    assert disabled.status_code == 200
    assert disabled.data["is_active"] is False
    assert disabled.data["command_revision"] == revision


def test_disable_keeps_old_auth_until_fresh_off_is_proven(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="safe-disable-root", password="pass12345",
    )
    device = _device()
    client = auth_client(superuser)

    pending = client.post(
        f"/api/conveyors/devices/{device.public_id}/disable/",
        {},
        format="json",
    )

    assert pending.status_code == 409
    assert pending.data["code"] == "off_not_confirmed"
    device.refresh_from_db()
    assert device.is_active is True
    assert device.stop_reason == "device_disable_pending"
    revision = device.command_revision

    fetched = _sync(api_client, device, seq=0)
    assert fetched.status_code == 200
    assert fetched.data["command"]["revision"] == revision
    assert fetched.data["command"]["state"] == 0
    acknowledged = _sync(
        api_client,
        device,
        seq=1,
        ack_revision=revision,
        output_state=0,
        feedback_state=0,
    )
    assert acknowledged.status_code == 200

    disabled = client.post(
        f"/api/conveyors/devices/{device.public_id}/disable/",
        {},
        format="json",
    )
    assert disabled.status_code == 200
    assert disabled.data["is_active"] is False


def test_disable_refuses_open_session_and_latches_off(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="busy-disable-root", password="pass12345",
    )
    device = _device(desired_state=True, command_terminal=False)
    _, session = _order_session(superuser)
    device.command_session = session
    device.command_target_total = session.target_total
    device.save(update_fields=["command_session", "command_target_total"])

    response = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/disable/",
        {},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "device_busy"
    device.refresh_from_db()
    assert device.is_active is True
    assert device.desired_state is False
    assert device.command_terminal is True
    assert device.stop_reason == "device_disable_pending"
    assert _sync(api_client, device, seq=0).status_code == 200


def test_patch_cannot_bypass_safe_disable(auth_client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username="patch-disable-root", password="pass12345",
    )
    device = _device()

    response = auth_client(superuser).patch(
        f"/api/conveyors/devices/{device.public_id}/",
        {"is_active": False},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "use_disable_endpoint"
    device.refresh_from_db()
    assert device.is_active is True


def test_rotate_keeps_old_secret_until_fresh_off_is_proven(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="safe-rotate-root", password="pass12345",
    )
    device = _device()
    old_digest = device.secret_sha256
    assert _sync(
        api_client, device, seq=0, output_state=1, feedback_state=1,
    ).status_code == 200

    response = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/rotate-secret/",
        {},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "off_not_confirmed"
    device.refresh_from_db()
    assert device.secret_sha256 == old_digest
    assert device.stop_reason == "credential_rotation_pending"
    assert _sync(api_client, device, seq=1).status_code == 200


def test_pending_disable_survives_transitional_on_telemetry(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="durable-disable-root", password="pass12345",
    )
    device = _device(desired_state=True, command_terminal=False)
    _, old_session = _order_session(superuser)
    device.command_session = old_session
    device.command_target_total = old_session.target_total
    device.save(update_fields=["command_session", "command_target_total"])

    pending = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/disable/",
        {},
        format="json",
    )
    assert pending.status_code == 409
    device.refresh_from_db()
    off_revision = device.command_revision

    transitional = _sync(
        api_client,
        device,
        seq=0,
        ack_revision=off_revision - 1,
        output_state=1,
        feedback_state=1,
    )
    assert transitional.status_code == 200
    device.refresh_from_db()
    assert device.stop_reason == "device_disable_pending"

    stopped = emergency_stop(device, "manual_stop")
    assert stopped.stop_reason == "device_disable_pending"
    off_revision = stopped.command_revision
    old_session.status = AiCountingSession.CLOSED
    old_session.save(update_fields=["status"])
    confirmed_off = _sync(
        api_client,
        device,
        seq=1,
        ack_revision=off_revision,
        output_state=0,
        feedback_state=0,
    )
    assert confirmed_off.status_code == 200
    _, new_session = _order_session(superuser, target=11)

    with pytest.raises(ConveyorDeviceError) as caught:
        prepare_session(new_session)

    assert caught.value.code == "device_transition_pending"


def test_sync_rechecks_presented_secret_under_device_lock():
    device = _device()
    presented_digest = device.secret_sha256
    device.secret_sha256 = digest_token("B" * 43)
    device.save(update_fields=["secret_sha256"])
    payload = _sync_body()
    payload["boot_id"] = BOOT_ID

    with pytest.raises(ConveyorDeviceError) as caught:
        sync_device(device.pk, payload, presented_digest)

    assert caught.value.code == "invalid_credential"
    device.refresh_from_db()
    assert device.last_seen_at is None


@pytest.mark.parametrize(
    ("pending_reason", "action"),
    [
        ("device_disable_pending", "rotate-secret"),
        ("credential_rotation_pending", "disable"),
    ],
)
def test_pending_admin_transition_cannot_be_silently_superseded(
    auth_client,
    django_user_model,
    pending_reason,
    action,
):
    superuser = django_user_model.objects.create_superuser(
        username=f"pending-{action}-root", password="pass12345",
    )
    device = _device(stop_reason=pending_reason)

    response = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/{action}/",
        {},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "device_transition_pending"
    device.refresh_from_db()
    assert device.stop_reason == pending_reason


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


def test_bench_pulse_emits_short_lease_then_terminally_auto_stops(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="bench-pulse-root", password="pass12345",
    )
    device = _ready_bench_device(api_client)
    previous_revision = device.command_revision

    started = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/bench-pulse/",
        {"confirmation": "ISOLATED_NO_MOTOR"},
        format="json",
    )

    assert started.status_code == 200, started.data
    assert started["Cache-Control"] == "no-store"
    assert started.data["desired_state"] == 1
    assert started.data["command_terminal"] is False
    assert started.data["stop_reason"] == "bench_pulse"
    assert started.data["pulse_window_ms"] == 500
    assert started.data["lease_ms"] == 500
    revision = started.data["command_revision"]
    assert revision == previous_revision + 1

    command = _sync(
        api_client,
        device,
        seq=2,
        ack_revision=previous_revision,
        firmware="1.0.0-bench-d15",
    )
    assert command.status_code == 200, command.data
    assert command.data["command"] == {
        "revision": revision,
        "state": 1,
        "lease_ms": 500,
        "session_id": device.pk,
        "target_total": 1,
        "reason": "bench_pulse",
    }

    device.refresh_from_db()
    device.run_started_at = timezone.now() - timedelta(seconds=1)
    device.save(update_fields=["run_started_at"])
    stopped = _sync(
        api_client,
        device,
        seq=3,
        ack_revision=revision,
        output_state=1,
        feedback_state=1,
        firmware="1.0.0-bench-d15",
    )
    assert stopped.status_code == 200, stopped.data
    assert stopped.data["command"]["state"] == 0
    assert stopped.data["command"]["lease_ms"] == 0
    assert stopped.data["command"]["session_id"] is None
    assert stopped.data["command"]["target_total"] is None
    assert stopped.data["command"]["revision"] == revision + 1
    assert stopped.data["command"]["reason"] == "bench_timeout"

    device.refresh_from_db()
    assert device.desired_state is False
    assert device.command_terminal is True
    assert device.stop_reason == "bench_timeout"
    event = EventLog.objects.get(event_type="conveyor_bench_pulse")
    assert event.payload == {
        "device_id": str(device.public_id),
        "camera": "cam2",
        "revision": revision,
        "pulse_window_ms": 500,
        "lease_ms": 500,
        "confirmation": "ISOLATED_NO_MOTOR",
    }


def test_bench_pulse_requires_superuser_and_exact_isolation_confirmation(
    auth_client, api_client, django_user_model,
):
    device = _ready_bench_device(api_client)
    regular = django_user_model.objects.create_user(
        username="bench-regular", password="pass12345",
    )
    superuser = django_user_model.objects.create_superuser(
        username="bench-confirm-root", password="pass12345",
    )
    url = f"/api/conveyors/devices/{device.public_id}/bench-pulse/"

    forbidden = auth_client(regular).post(
        url,
        {"confirmation": "ISOLATED_NO_MOTOR"},
        format="json",
    )
    assert forbidden.status_code == 403

    client = auth_client(superuser)
    for body in (
        {},
        {"confirmation": "yes"},
        {"confirmation": True},
        {"confirmation": "ISOLATED_NO_MOTOR", "duration_ms": 500},
    ):
        rejected = client.post(url, body, format="json")
        assert rejected.status_code == 400

    device.refresh_from_db()
    assert device.desired_state is False
    assert device.command_revision == 1
    assert not EventLog.objects.filter(event_type="conveyor_bench_pulse").exists()


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"firmware": "1.0.0"}, "bench_firmware_required"),
        (
            {"last_seen_at": timezone.now() - timedelta(seconds=10)},
            "off_not_confirmed",
        ),
        ({"last_ack_revision": None}, "off_not_confirmed"),
        ({"output_state": True}, "off_not_confirmed"),
        ({"feedback_state": True}, "off_not_confirmed"),
        ({"fault": "feedback_failed_on"}, "off_not_confirmed"),
        (
            {"desired_state": True, "command_terminal": False},
            "device_busy",
        ),
        (
            {"stop_reason": "credential_rotation_pending"},
            "device_transition_pending",
        ),
    ],
)
def test_bench_pulse_rejects_every_unsafe_device_state(
    auth_client, django_user_model, overrides, error_code,
):
    superuser = django_user_model.objects.create_superuser(
        username=f"bench-unsafe-{error_code}-{len(overrides)}",
        password="pass12345",
    )
    values = {
        "last_seen_at": timezone.now(),
        "last_boot_id": BOOT_ID,
        "last_sequence": 1,
        "last_ack_revision": 1,
        "output_state": False,
        "feedback_state": False,
        "fault": "",
        "firmware": "1.0.0-bench-d15",
    }
    values.update(overrides)
    device = _device(**values)

    response = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/bench-pulse/",
        {"confirmation": "ISOLATED_NO_MOTOR"},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == error_code
    device.refresh_from_db()
    assert device.command_revision == 1
    assert not EventLog.objects.filter(event_type="conveyor_bench_pulse").exists()


def test_bench_pulse_rejects_an_open_camera_session(
    auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="bench-open-session-root", password="pass12345",
    )
    device = _device(
        last_seen_at=timezone.now(),
        last_boot_id=BOOT_ID,
        last_sequence=1,
        last_ack_revision=1,
        output_state=False,
        feedback_state=False,
        firmware="1.0.0-bench-d15",
    )
    _order_session(superuser)

    response = auth_client(superuser).post(
        f"/api/conveyors/devices/{device.public_id}/bench-pulse/",
        {"confirmation": "ISOLATED_NO_MOTOR"},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "device_busy"
    device.refresh_from_db()
    assert device.command_revision == 1
    assert device.desired_state is False


def test_emergency_stop_preempts_an_unfetched_bench_pulse(
    api_client, auth_client, django_user_model,
):
    superuser = django_user_model.objects.create_superuser(
        username="bench-stop-root", password="pass12345",
    )
    device = _ready_bench_device(api_client)
    client = auth_client(superuser)
    started = client.post(
        f"/api/conveyors/devices/{device.public_id}/bench-pulse/",
        {"confirmation": "ISOLATED_NO_MOTOR"},
        format="json",
    )
    on_revision = started.data["command_revision"]

    stopped = client.post(
        f"/api/conveyors/devices/{device.public_id}/emergency-stop/",
        {},
        format="json",
    )

    assert stopped.status_code == 200
    assert stopped.data["desired_state"] == 0
    assert stopped.data["command_terminal"] is True
    assert stopped.data["command_revision"] == on_revision + 1
    command = _sync(
        api_client,
        device,
        seq=2,
        ack_revision=1,
        firmware="1.0.0-bench-d15",
    )
    assert command.data["command"]["state"] == 0


def test_bench_pulse_rolls_back_if_audit_write_fails(
    api_client, auth_client, django_user_model, monkeypatch,
):
    superuser = django_user_model.objects.create_superuser(
        username="bench-audit-root", password="pass12345",
    )
    device = _ready_bench_device(api_client)

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("apps.conveyors.views.log_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        auth_client(superuser).post(
            f"/api/conveyors/devices/{device.public_id}/bench-pulse/",
            {"confirmation": "ISOLATED_NO_MOTOR"},
            format="json",
        )

    device.refresh_from_db()
    assert device.command_revision == 1
    assert device.desired_state is False
    assert device.command_terminal is True
