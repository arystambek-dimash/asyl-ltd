import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.orders.services import add_payment
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _order(boss, status="shipped"):
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status=status)
    OrderItem.objects.create(order=o, product=p, quantity=2)  # total 200
    return o


def test_payment_blocked_before_shipped(boss):
    o = _order(boss, status="loading")
    with pytest.raises(ValidationError) as e:
        add_payment(o, "100", boss)
    assert e.value.detail["code"] == "payment_not_open"


def test_partial_then_full_payment(boss):
    o = _order(boss, status="shipped")
    add_payment(o, "100", boss)
    o.refresh_from_db()
    assert o.payment_status == "partial"
    add_payment(o, "100", boss)
    o.refresh_from_db()
    assert o.payment_status == "settled"
