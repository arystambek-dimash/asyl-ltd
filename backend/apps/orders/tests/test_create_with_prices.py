import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from apps.catalog.models import Product, ClientPrice
from apps.warehouse.models import StockItem
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient(); c.force_authenticate(user); return c


def test_staff_create_with_prices_confirms_immediately(manager):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = _api(manager).post("/api/orders/", {
        "client": c.id,
        "items": [{"product": p.id, "quantity": 3}],
        "prices": {str(p.id): "15000"},   # цена по товару
    }, format="json")
    assert r.status_code == 201
    o = Order.objects.get()
    assert o.status == "confirmed"
    assert o.total_amount == Decimal("45000.00")
    assert ClientPrice.objects.get(client=c, product=p).price == Decimal("15000.00")


def test_staff_create_without_prices_stays_draft(manager):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = _api(manager).post("/api/orders/", {
        "client": c.id,
        "items": [{"product": p.id, "quantity": 1}],
    }, format="json")
    assert r.status_code == 201
    assert Order.objects.get().status == "draft"
