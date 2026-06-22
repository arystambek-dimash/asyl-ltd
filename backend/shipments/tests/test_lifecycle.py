import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock
from rest_framework.exceptions import ValidationError
from shipments.services import (record_arrival, start_loading, record_count,
                                finish_loading, record_shipment)

pytestmark = pytest.mark.django_db


def _paid_order(boss, status="paid", bags_in_stock=100, qty=50):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, bags_in_stock, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if status == "paid":
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def test_arrive_requires_payment(boss, operator):
    o, _ = _paid_order(boss, status="confirmed")
    with pytest.raises(ValidationError):
        record_arrival(o, Decimal("8000"), operator)


def test_boss_debt_override_allows_arrival(boss):
    o, _ = _paid_order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), boss, debt_override=True)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.debt_override is True


def test_full_flow_deducts_stock_and_computes_net(boss, operator):
    o, prod = _paid_order(boss, status="paid", bags_in_stock=100, qty=50)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10500"), operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.shipment.net_weight_kg == Decimal("2500")
    from warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50


def test_double_ship_rejected(boss, operator):
    o, _ = _paid_order(boss)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10500"), operator)
    with pytest.raises(ValidationError):
        record_shipment(o, Decimal("10500"), operator)
