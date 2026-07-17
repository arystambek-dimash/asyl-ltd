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
    client = Client.objects.create(first_name="Board", last_name="Client", phone="1")
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
    client = Client.objects.create(first_name="Board", last_name="History", phone="2")
    recent = _order(client, "shipped", timezone.now() - timedelta(days=2))
    old = _order(client, "shipped", timezone.now() - timedelta(days=3))

    response = auth_client(operator).get("/api/orders/?post_board=1")

    ids = {item["id"] for item in response.data}
    assert recent.id in ids
    assert old.id not in ids
