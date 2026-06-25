import pytest
from decimal import Decimal
from apps.catalog.models import Product, ClientPrice
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def test_client_price_unique_per_client_product():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    ClientPrice.objects.create(client=c, product=p, price="10000.00")
    cp = ClientPrice.objects.get(client=c, product=p)
    assert cp.price == Decimal("10000.00")


def test_total_uses_unit_price_when_set():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c)
    # unit_price (договорная) перекрывает базовую цену товара
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="10000.00")
    assert o.total_amount == Decimal("20000.00")


def test_total_falls_back_to_product_price_when_unit_price_null():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c)
    OrderItem.objects.create(order=o, product=p, quantity=2)  # unit_price = None
    assert o.total_amount == Decimal("200.00")
