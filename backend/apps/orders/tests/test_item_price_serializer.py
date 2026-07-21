import pytest
from decimal import Decimal
from apps.catalog.models import Product, ClientPrice
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import OrderSerializer

pytestmark = pytest.mark.django_db


def test_item_exposes_client_price_hint_and_unit_price():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    ClientPrice.objects.create(client=c, product=p, price="10000.00")
    o = Order.objects.create(client=c, status="pending")
    OrderItem.objects.create(order=o, product=p, quantity=2)  # unit_price None

    item = OrderSerializer(o).data["items"][0]
    assert item["unit_price"] is None
    assert item["client_price"] == "10000.00"   # подсказка для предзаполнения
    assert "base_price" not in item
    assert item["price"] is None


def test_price_reflects_unit_price_after_confirm():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="10000.00")

    item = OrderSerializer(o).data["items"][0]
    assert item["unit_price"] == "10000.00"
    assert item["price"] == "10000.00"


def test_item_price_hint_uses_order_currency():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="Currency P", color="Blue", weight_kg="25")
    ClientPrice.objects.create(client=c, product=p, currency="KZT", price="10000.00")
    ClientPrice.objects.create(client=c, product=p, currency="USD", price="21.50")
    order = Order.objects.create(client=c, status="pending", currency="USD")
    OrderItem.objects.create(order=order, product=p, quantity=1)

    item = OrderSerializer(order).data["items"][0]
    assert item["client_price"] == "21.50"


def test_deleted_product_keeps_historical_order_item_snapshot():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="Исторический", color="Blue", weight_kg="25")
    o = Order.objects.create(client=c, status="shipped")
    line = OrderItem.objects.create(
        order=o, product=p, quantity=3, unit_price="7500.00")
    expected_label = str(p)

    p.delete()
    line.refresh_from_db()
    item = OrderSerializer(o).data["items"][0]

    assert line.product_id is None
    assert item["product"] is None
    assert item["product_label"] == expected_label
    assert item["cv_class"] == "Blue_25"
    assert item["weight_kg"] == "25.00"
    assert o.total_amount == Decimal("22500.00")
