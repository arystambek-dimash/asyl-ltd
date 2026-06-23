import pytest
from decimal import Decimal
from catalog.models import Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock, deduct_stock
from warehouse.models import StockItem
from shipments.services import (record_arrival, start_loading, record_count,
                                finish_loading, record_shipment)
from webhooks.models import VideoJob

pytestmark = pytest.mark.django_db


def _setup(boss, bags_in_stock=100):
    red = Product.objects.create(name="Высший", color="Red", weight_kg="50", price="25000")
    receive_stock(red, bags_in_stock, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status="paid", truck_number="01A123")
    OrderItem.objects.create(order=o, product=red, quantity=50)
    Payment.objects.create(order=o, amount=o.total_amount)
    return o, red


def test_deduct_allow_negative_goes_below_zero(boss):
    p = Product.objects.create(name="X", color="Blue", weight_kg="25", price="1")
    receive_stock(p, 5, boss)
    deduct_stock(p, 8, boss, allow_negative=True)
    assert StockItem.objects.get(product=p).bags == -3


def test_shipment_deducts_by_cv_counts(boss, operator):
    o, red = _setup(boss, bags_in_stock=100)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 40, operator)
    finish_loading(o, operator)
    VideoJob.objects.create(order=o, status="done", bags_counted=40,
                            counts_by_class={"Red_50": 40})
    record_shipment(o, Decimal("10000"), operator)
    assert StockItem.objects.get(product=red).bags == 60  # 100 - 40 by CV


def test_shipment_fallback_to_orderitems_without_video(boss, operator):
    o, red = _setup(boss, bags_in_stock=100)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10000"), operator)  # no VideoJob → OrderItem path
    assert StockItem.objects.get(product=red).bags == 50  # 100 - 50 ordered
