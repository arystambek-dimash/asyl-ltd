import pytest
from decimal import Decimal
from clients.models import Client
from catalog.models import Product
from orders.models import Order, OrderItem, Payment


@pytest.fixture
def order(db):
    client = Client.objects.create(first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="Flour", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=client, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=2)
    return o


def test_new_statuses_present():
    assert "pending" in Order.STATUSES
    assert "rejected" in Order.STATUSES


def test_paid_total_counts_only_confirmed(order):
    Payment.objects.create(order=order, amount=Decimal("50"), status="pending")
    Payment.objects.create(order=order, amount=Decimal("100"), status="confirmed")
    assert order.paid_total == Decimal("100")


def test_new_order_defaults(order):
    assert order.debt_requested is False
    assert order.truck_number_set_by is None
