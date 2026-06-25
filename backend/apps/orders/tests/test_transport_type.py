import pytest
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


def test_order_defaults_to_truck():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c)
    assert o.transport_type == "truck"
    assert "train" in Order.TRANSPORT_TYPES


def test_order_can_be_train():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, transport_type="train")
    assert o.transport_type == "train"
