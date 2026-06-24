import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.warehouse.services import receive_stock
from rest_framework.exceptions import ValidationError
from apps.shipments.services import (record_arrival, start_loading, record_count,
                                finish_loading, record_shipment)

pytestmark = pytest.mark.django_db


def _order(boss, status="confirmed", qty=50):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
    receive_stock(prod, 100, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if status in ("paid", "loading", "loaded", "shipped"):
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def _arrive_and_pay(o, boss, operator):
    """Провести заказ confirmed → arrived (въезд) → paid (оплата)."""
    record_arrival(o, Decimal("8000"), operator)
    Payment.objects.create(order=o, amount=o.total_amount)
    from apps.orders.services import _maybe_mark_paid
    _maybe_mark_paid(o, boss)
    o.refresh_from_db()


def test_arrival_uses_order_truck_number(boss, operator):
    o, _ = _order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), operator)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.shipment.weigh_in_kg == Decimal("8000")
    assert o.shipment.truck_number == "01A123"


def test_start_loading_requires_paid(boss, operator):
    o, _ = _order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), operator)
    with pytest.raises(ValidationError):  # arrived, но ещё не оплачен
        start_loading(o, operator)
    Payment.objects.create(order=o, amount=o.total_amount)
    from apps.orders.services import _maybe_mark_paid
    _maybe_mark_paid(o, boss)
    start_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loading"


def test_record_count_from_paid_auto_advances(boss, operator):
    o, _ = _order(boss, status="confirmed")
    _arrive_and_pay(o, boss, operator)
    record_count(o, 50, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 50


def test_record_count_does_not_reach_loaded(boss, operator):
    o, _ = _order(boss, status="confirmed")
    _arrive_and_pay(o, boss, operator)
    start_loading(o, operator)
    record_count(o, 42, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 42


def test_finish_loading_requires_loading(boss, operator):
    o, _ = _order(boss, status="confirmed")
    _arrive_and_pay(o, boss, operator)
    with pytest.raises(ValidationError):
        finish_loading(o, operator)
    start_loading(o, operator)
    finish_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loaded"


def test_shipment_requires_loaded(boss, operator):
    o, prod = _order(boss, status="confirmed")
    _arrive_and_pay(o, boss, operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    with pytest.raises(ValidationError):  # still loading, not loaded
        record_shipment(o, operator)
    finish_loading(o, operator)
    record_shipment(o, operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    from apps.warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50
