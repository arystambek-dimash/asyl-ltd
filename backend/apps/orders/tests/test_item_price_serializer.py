import pytest
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
    assert item["base_price"] == "100.00"
    assert item["price"] == "100.00"            # пока договорная не задана — базовая


def test_price_reflects_unit_price_after_confirm():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="10000.00")

    item = OrderSerializer(o).data["items"][0]
    assert item["unit_price"] == "10000.00"
    assert item["price"] == "10000.00"
