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


def _order(boss, status="confirmed", bags_in_stock=100, qty=50):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
    receive_stock(prod, bags_in_stock, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    # В новом потоке оплата идёт после въезда. Для статусов от "paid" и выше
    # сразу проставляем подтверждённую оплату.
    if status in ("paid", "loading", "loaded", "shipped"):
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def test_arrive_works_without_payment(boss, operator):
    # Въезд разрешён сразу после подтверждения, без оплаты.
    o, _ = _order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), operator)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.shipment.weigh_in_kg == Decimal("8000")


def test_arrive_requires_confirmed(boss, operator):
    o, _ = _order(boss, status="pending")
    with pytest.raises(ValidationError):
        record_arrival(o, Decimal("8000"), operator)


def test_full_flow_deducts_stock(boss, operator):
    o, prod = _order(boss, status="confirmed", bags_in_stock=100, qty=50)
    record_arrival(o, Decimal("8000"), operator)
    # Оплата после въезда → переход в "paid".
    Payment.objects.create(order=o, amount=o.total_amount)
    from apps.orders.services import _maybe_mark_paid
    _maybe_mark_paid(o, boss)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.shipment.weigh_in_kg == Decimal("8000")
    assert o.shipment.shipped_at is not None
    from apps.warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50


def test_double_ship_rejected(boss, operator):
    o, _ = _order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), operator)
    Payment.objects.create(order=o, amount=o.total_amount)
    from apps.orders.services import _maybe_mark_paid
    _maybe_mark_paid(o, boss)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, operator)
    with pytest.raises(ValidationError):
        record_shipment(o, operator)
