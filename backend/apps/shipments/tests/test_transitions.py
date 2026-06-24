import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
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
    return o, prod


def _arrive(o, operator):
    """Провести заказ confirmed → arrived (въезд, без оплаты)."""
    record_arrival(o, Decimal("8000"), operator)
    o.refresh_from_db()


def test_arrival_uses_order_truck_number(boss, operator):
    o, _ = _order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), operator)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.shipment.weigh_in_kg == Decimal("8000")
    assert o.shipment.truck_number == "01A123"


def test_start_loading_requires_arrived(boss, operator):
    o, _ = _order(boss, status="confirmed")
    with pytest.raises(ValidationError):  # confirmed, машина ещё не въехала
        start_loading(o, operator)
    _arrive(o, operator)
    start_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loading"


def test_record_count_from_arrived_auto_advances(boss, operator):
    o, _ = _order(boss, status="confirmed")
    _arrive(o, operator)
    record_count(o, 50, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 50


def test_record_count_does_not_reach_loaded(boss, operator):
    o, _ = _order(boss, status="confirmed")
    _arrive(o, operator)
    start_loading(o, operator)
    record_count(o, 42, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 42


def test_finish_loading_requires_loading(boss, operator):
    o, _ = _order(boss, status="confirmed")
    _arrive(o, operator)
    with pytest.raises(ValidationError):
        finish_loading(o, operator)
    start_loading(o, operator)
    finish_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loaded"


def test_shipment_requires_loaded(boss, operator):
    o, prod = _order(boss, status="confirmed")
    _arrive(o, operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    with pytest.raises(ValidationError):  # still loading, not loaded
        record_shipment(o, operator)
    finish_loading(o, operator)
    record_shipment(o, operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.payment_status == "unpaid"
    from apps.warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50
