from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cameras.models import MonoblockCameraSettings
from apps.clients.models import Client
from apps.orders.models import Order
from apps.shipments.models import Shipment


pytestmark = pytest.mark.django_db


def _order(client, status, shipped_at=None):
    order = Order.objects.create(client=client, status=status)
    if shipped_at is not None:
        Shipment.objects.create(order=order, shipped_at=shipped_at)
    return order


def test_post_board_defaults_to_active_orders_and_todays_completed(auth_client, operator):
    client = Client.objects.create_with_user(first_name="Board", last_name="Client", phone="1")
    active = _order(client, "loading")
    today = _order(client, "shipped", timezone.now())
    old = _order(client, "shipped", timezone.now() - timedelta(days=1))
    _order(client, "pending")

    response = auth_client(operator).get("/api/orders/?post_board=1")

    assert response.status_code == 200
    assert {item["id"] for item in response.data} == {active.id, today.id}
    assert old.id not in {item["id"] for item in response.data}


def test_post_board_uses_admin_completed_days(auth_client, operator):
    MonoblockCameraSettings.objects.create(completed_orders_days=3)
    client = Client.objects.create_with_user(first_name="Board", last_name="History", phone="2")
    recent = _order(client, "shipped", timezone.now() - timedelta(days=2))
    old = _order(client, "shipped", timezone.now() - timedelta(days=3))

    response = auth_client(operator).get("/api/orders/?post_board=1")

    ids = {item["id"] for item in response.data}
    assert recent.id in ids
    assert old.id not in ids


def test_post_board_is_available_to_train_loader(
    auth_client, user_with_perms
):
    loader = user_with_perms(
        "board-train-loader",
        codes=["train.view", "train.load"],
    )
    client = Client.objects.create_with_user(
        first_name="Train", last_name="Loader", phone="3"
    )
    active = _order(client, "confirmed")

    response = auth_client(loader).get("/api/orders/?post_board=1")

    assert response.status_code == 200
    assert {item["id"] for item in response.data} == {active.id}


def test_dashboard_operational_returns_authoritative_data(
    auth_client, operator
):
    from apps.eventlog.models import EventLog

    client = Client.objects.create_with_user(
        first_name="Dashboard", last_name="Operator", phone="4"
    )
    loading = _order(client, "loading")
    _order(client, "pending")
    shipped = _order(client, "shipped", timezone.now())
    EventLog.objects.create(
        event_type="shipment",
        message="Отгружено",
        order=shipped,
        payload={"bags_loaded": 12},
    )
    today = timezone.localdate().isoformat()

    response = auth_client(operator).get(
        f"/api/orders/dashboard-operational/?from={today}&to={today}"
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.data["queue"]] == [loading.id]
    assert response.data["attention"] == {
        "pending_payments": 0,
        "awaiting_review": 1,
        "stuck_in_loading": 1,
    }
    assert response.data["days"] == [
        {"date": today, "bags": 12, "orders": 1}
    ]
