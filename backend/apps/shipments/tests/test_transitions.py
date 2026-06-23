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


def _order(boss, status="paid", qty=50):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
    receive_stock(prod, 100, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if status == "paid":
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def test_arrival_uses_order_truck_number(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.shipment.weigh_in_kg == Decimal("8000")
    assert o.shipment.truck_number == "01A123"


def test_start_loading_requires_arrived(boss, operator):
    o, _ = _order(boss, status="paid")
    with pytest.raises(ValidationError):
        start_loading(o, operator)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loading"


def test_record_count_from_arrived_auto_advances(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    record_count(o, 50, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 50


def test_record_count_does_not_reach_loaded(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 42, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 42


def test_finish_loading_requires_loading(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    with pytest.raises(ValidationError):
        finish_loading(o, operator)
    start_loading(o, operator)
    finish_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loaded"


def test_shipment_requires_loaded_and_computes_net(boss, operator):
    o, prod = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    with pytest.raises(ValidationError):  # still loading, not loaded
        record_shipment(o, Decimal("10500"), operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10500"), operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.shipment.net_weight_kg == Decimal("2500")
    from apps.warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50
