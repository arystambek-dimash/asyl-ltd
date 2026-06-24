import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def test_order_defaults_debt_and_unpaid():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c)
    assert o.payment_status == "unpaid"
    assert o.settlement_intent == "debt"
    assert "paid" not in Order.STATUSES


def test_remaining_amount():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c)
    OrderItem.objects.create(order=o, product=p, quantity=2)
    assert o.remaining_amount == Decimal("200.00")
