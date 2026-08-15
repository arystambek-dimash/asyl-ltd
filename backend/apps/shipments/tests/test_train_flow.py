import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.cameras.models import AiCountingSession
from apps.warehouse.services import receive_stock
from apps.warehouse.models import StockItem
from apps.shipments.services import (
    finish_train_loading,
    record_arrival,
    record_count,
    record_shipment,
    start_train_loading,
)
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _train_order(boss, status="confirmed", qty=50, stock=100):
    prod = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    receive_stock(prod, stock, boss)
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status=status, transport_type="train")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    return o, prod


def test_train_finish_and_shipment_are_separate_transitions(boss):
    o, prod = _train_order(boss, qty=50, stock=100)
    start_train_loading(o, boss)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.loading_started_at is not None
    assert o.shipment.truck_number == ""  # без номера машины

    record_count(o, 50, boss)
    finish_train_loading(o, boss)
    o.refresh_from_db()
    assert o.status == "loaded"
    assert o.shipment.shipped_at is None
    assert StockItem.objects.get(product=prod).bags == 100

    record_shipment(o, boss)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.payment_status == "unpaid"
    assert o.shipment.shipped_at is not None
    assert StockItem.objects.get(product=prod).bags == 50


def test_train_finish_cannot_bypass_open_ai_session(boss):
    order, product = _train_order(boss, qty=50, stock=100)
    start_train_loading(order, boss)
    record_count(order, 25, boss)
    AiCountingSession.objects.create(
        order=order,
        camera="cam6",
        status=AiCountingSession.ACTIVE,
        started_by=boss,
    )

    with pytest.raises(ValidationError) as exc:
        finish_train_loading(order, boss)

    assert exc.value.detail["code"] == "ai_session_active"
    order.refresh_from_db()
    assert order.status == "loading"
    assert order.shipment.shipped_at is None
    assert StockItem.objects.get(product=product).bags == 100


def test_train_start_requires_confirmed(boss):
    o, _ = _train_order(boss, status="pending")
    with pytest.raises(ValidationError):
        start_train_loading(o, boss)


def test_train_service_rejects_truck_order(boss):
    prod = Product.objects.create(name="T", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="confirmed", transport_type="truck")
    OrderItem.objects.create(order=o, product=prod, quantity=1)
    with pytest.raises(ValidationError) as e:
        start_train_loading(o, boss)
    assert e.value.detail["code"] == "wrong_transport"


def test_truck_arrival_rejects_train_order(boss):
    o, _ = _train_order(boss)
    with pytest.raises(ValidationError) as e:
        record_arrival(o, Decimal("8000"), boss)
    assert e.value.detail["code"] == "wrong_transport"
