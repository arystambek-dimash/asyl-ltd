import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cameras import ai, sessions
from apps.cameras.models import (
    AiCountingSession,
    MonoblockCameraSettings,
)
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.conveyors import services as conveyor_services
from apps.conveyors.credentials import digest_token
from apps.conveyors.models import ConveyorDevice
from apps.grain import scale as grain_scale
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def conveyor_settings(settings, monkeypatch):
    settings.CONVEYOR_COMMAND_TIMEOUT_SECONDS = 1
    settings.CONVEYOR_LEGACY_BRIDGE_CAMERAS = frozenset()
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    MonoblockCameraSettings.objects.create(camera_sources=["cam2"])


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("cloud-count-loader", codes=["shipping.load"])


def _order(*, status="confirmed", target=10, product=False):
    client = Client.objects.create_with_user(
        first_name="Cloud", last_name="Flow", phone=f"cloud-flow-{status}-{target}"
    )
    order = Order.objects.create(
        client=client,
        status=status,
        truck_number="01CLOUD",
        loading_camera="cam2" if status == "loading" else "",
    )
    item_product = None
    if product:
        item_product = Product.objects.create(
            name=f"Cloud product {target}",
            color="White",
            weight_kg="50",
            price="100.00",
        )
    OrderItem.objects.create(
        order=order,
        product=item_product,
        quantity=target,
    )
    return order


def _device(**overrides):
    defaults = {
        "last_seen_at": timezone.now(),
        "last_boot_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
        "last_sequence": 0,
        "last_ack_revision": 1,
        "output_state": False,
        "feedback_state": False,
    }
    defaults.update(overrides)
    return ConveyorDevice.objects.create(
        name="Cloud line",
        camera_source="cam2",
        secret_sha256=digest_token("C" * 43),
        **defaults,
    )


def _mark_prepared(session, _timeout):
    device = ConveyorDevice.objects.get(camera_source=session.camera)
    now = timezone.now()
    device.last_seen_at = now
    device.last_boot_id = "11111111-1111-4111-8111-111111111111"
    device.last_ack_revision = device.command_revision
    device.output_state = False
    device.feedback_state = False
    device.last_ai_seen_at = now
    device.last_ai_boot_id = "22222222-2222-4222-8222-222222222222"
    device.last_ai_sequence = 0
    device.last_total = 0
    device.save()
    return device


def _confirm(device_id, revision, desired, _timeout, *, seen_after=None):
    device = ConveyorDevice.objects.get(pk=device_id)
    device.last_ack_revision = revision
    device.output_state = desired
    device.feedback_state = desired
    device.last_seen_at = (
        seen_after + timedelta(milliseconds=1)
        if seen_after is not None
        else timezone.now()
    )
    device.save()
    return device


def test_bind_cloud_uses_only_cloud_master_and_freezes_transport(
    auth_client, loader,
):
    order = _order(target=12)
    _device()
    edge = {
        "cam": "cam2",
        "running": True,
        "mode": "session",
        "stream": "cam2ai",
        "total": 0,
    }

    with (
        patch.object(ai, "start_order_session", return_value=(edge, True)) as start,
        patch.object(ai, "start_conveyor") as direct_start,
        patch.object(ai, "emergency_stop_conveyor") as direct_stop,
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_prepared",
            side_effect=_mark_prepared,
        ),
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_confirmed",
            side_effect=_confirm,
        ),
    ):
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200, response.data
    session = AiCountingSession.objects.get(order=order)
    device = ConveyorDevice.objects.get(camera_source="cam2")
    assert session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD
    assert session.conveyor_observation_mode == AiCountingSession.OBSERVATION_EDGE
    assert session.legacy_bridge_boot_id is None
    assert session.conveyor_enabled is True
    assert device.command_session_id == session.pk
    assert device.desired_state is True
    assert response.data["conveyor"]["transport"] == "cloud"
    start.assert_called_once_with(
        "cam2",
        session.pk,
        12,
        initialize_legacy_worker=True,
        conveyor_transport="cloud",
    )
    direct_start.assert_not_called()
    direct_stop.assert_not_called()


def test_legacy_cloud_starts_and_zeros_old_counter_before_prepare(
    auth_client, loader, settings,
):
    settings.CONVEYOR_LEGACY_BRIDGE_CAMERAS = frozenset({"cam2"})
    order = _order(target=12)
    _device()
    events = []
    idle = {"cam": "cam2", "running": False, "mode": "idle", "total": 9}
    started = {"cam": "cam2", "running": True, "mode": "session", "total": 9}
    zeroed = {"cam": "cam2", "running": True, "mode": "session", "total": 0}
    real_prepare = conveyor_services.prepare_session

    def prepare(session):
        events.append("prepare")
        assert session.legacy_bridge_boot_id is None
        return real_prepare(session)

    def prepared_by_monitor(session, _timeout):
        events.append("wait_prepared")
        bridge_boot_id = "22222222-2222-4222-8222-222222222222"
        AiCountingSession.objects.filter(pk=session.pk).update(
            legacy_bridge_boot_id=bridge_boot_id,
        )
        session.legacy_bridge_boot_id = bridge_boot_id
        device = ConveyorDevice.objects.get(camera_source=session.camera)
        now = timezone.now()
        device.last_seen_at = now
        device.last_boot_id = "11111111-1111-4111-8111-111111111111"
        device.last_ack_revision = device.command_revision
        device.output_state = False
        device.feedback_state = False
        device.last_ai_seen_at = now
        device.last_ai_boot_id = bridge_boot_id
        device.last_ai_sequence = 0
        device.last_total = 0
        device.save()
        return device

    def old_status(_camera):
        events.append("status")
        return idle

    def old_start(_camera):
        events.append("start")
        return started

    def old_reset(_camera):
        events.append("reset")
        return zeroed

    with (
        patch.object(ai, "status", side_effect=old_status),
        patch.object(ai, "start", side_effect=old_start),
        patch.object(ai, "reset", side_effect=old_reset),
        patch.object(ai, "start_order_session") as native_start,
        patch(
            "apps.cameras.counting.cloud_conveyors.prepare_session",
            side_effect=prepare,
        ),
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_prepared",
            side_effect=prepared_by_monitor,
        ),
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_confirmed",
            side_effect=_confirm,
        ),
    ):
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200, response.data
    assert events == ["status", "start", "reset", "prepare", "wait_prepared"]
    session = AiCountingSession.objects.get(order=order)
    assert session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD
    assert (
        session.conveyor_observation_mode
        == AiCountingSession.OBSERVATION_LEGACY_BRIDGE
    )
    assert str(session.legacy_bridge_boot_id) == (
        "22222222-2222-4222-8222-222222222222"
    )
    native_start.assert_not_called()


def test_legacy_observation_mode_is_frozen_on_existing_reservation(
    loader, settings,
):
    order = _order(target=7)
    settings.CONVEYOR_LEGACY_BRIDGE_CAMERAS = frozenset({"cam2"})
    session, created = sessions.reserve(
        order,
        "cam2",
        loader,
        target_total=7,
        conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
    )
    assert created is True
    assert (
        session.conveyor_observation_mode
        == AiCountingSession.OBSERVATION_LEGACY_BRIDGE
    )
    assert session.legacy_bridge_boot_id is None

    settings.CONVEYOR_LEGACY_BRIDGE_CAMERAS = frozenset()
    same_session, created = sessions.reserve(
        order,
        "cam2",
        loader,
        target_total=7,
        conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
    )

    assert created is False
    assert same_session.pk == session.pk
    assert (
        same_session.conveyor_observation_mode
        == AiCountingSession.OBSERVATION_LEGACY_BRIDGE
    )


def test_numberless_cloud_start_never_reads_scale_or_records_arrival(
    auth_client, loader, settings,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order = _order(target=12)
    order.truck_number = ""
    order.save(update_fields=["truck_number"])
    _device()
    edge = {
        "cam": "cam2",
        "running": True,
        "mode": "session",
        "stream": "cam2ai",
        "total": 0,
    }
    with (
        patch.object(
            grain_scale,
            "read_truck_scale",
            side_effect=AssertionError("Cloud AI must not read Grain scales"),
        ) as scale_read,
        patch.object(ai, "start_order_session", return_value=(edge, True)),
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_prepared",
            side_effect=_mark_prepared,
        ),
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_confirmed",
            side_effect=_confirm,
        ),
    ):
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200, response.data
    session = AiCountingSession.objects.get(order=order)
    shipment = Shipment.objects.get(order=order)
    assert session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD
    assert shipment.truck_number == ""
    assert shipment.weigh_in_kg is None
    assert shipment.arrived_at is None
    assert shipment.shipped_at is None
    scale_read.assert_not_called()


def test_cloud_emergency_stop_never_calls_direct_modbus_endpoint(
    auth_client, loader,
):
    order = _order(status="loading", target=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
        conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
    )
    _device(
        desired_state=True,
        command_terminal=False,
        command_session=session,
        command_target_total=10,
        output_state=True,
        feedback_state=True,
        last_total=4,
        stop_reason="active_session",
    )

    with (
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_confirmed",
            side_effect=_confirm,
        ),
        patch.object(
            ai, "status", return_value={"running": True, "total": 4},
        ),
        patch.object(ai, "emergency_stop_conveyor") as direct_emergency,
        patch.object(ai, "stop_conveyor") as direct_stop,
    ):
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/conveyor/stop/",
            {"order_id": order.pk, "session_id": session.pk},
            format="json",
        )

    assert response.status_code == 200, response.data
    assert response.data["conveyor"]["desired"] == 0
    assert response.data["conveyor"]["feedback"] == 0
    direct_emergency.assert_not_called()
    direct_stop.assert_not_called()


def test_cloud_completion_uses_fresh_off_heartbeat_without_shipping(
    auth_client, loader,
):
    order = _order(status="loading", target=10, product=True)
    Shipment.objects.create(order=order, truck_number=order.truck_number)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
        conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
    )
    _device(
        desired_state=False,
        command_terminal=True,
        command_session=session,
        command_target_total=10,
        output_state=False,
        feedback_state=False,
        last_total=10,
        stop_reason="target_reached",
    )

    with (
        patch.object(
            grain_scale,
            "read_truck_scale",
            side_effect=AssertionError("Cloud completion must not read Grain scales"),
        ) as scale_read,
        patch(
            "apps.cameras.counting.cloud_conveyors.wait_confirmed",
            side_effect=_confirm,
        ) as wait,
        patch.object(
            ai, "status", return_value={"running": True, "total": 10},
        ),
        patch.object(ai, "delete", return_value={}),
        patch.object(ai, "emergency_stop_conveyor") as direct_emergency,
        patch.object(ai, "stop_conveyor") as direct_stop,
    ):
        response = auth_client(loader).delete(
            "/api/cameras/cam2/ai/",
            {
                "order_id": order.pk,
                "session_id": session.pk,
                "complete_order": True,
            },
            format="json",
        )

    assert response.status_code == 200, response.data
    assert response.data["order_status"] == "loaded"
    assert wait.call_count == 2
    assert wait.call_args_list[1].kwargs["seen_after"] is not None
    scale_read.assert_not_called()
    direct_emergency.assert_not_called()
    direct_stop.assert_not_called()
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED
    assert session.final_total == 10
    assert order.status == "loaded"
    assert order.is_debt is False
    assert order.shipment.shipped_at is None
    product_id = order.items.values_list("product_id", flat=True).get()
    assert not StockItem.objects.filter(product_id=product_id).exists()
