import pytest
from apps.catalog.models import Product
from apps.warehouse.models import StockItem
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


def _product():
    p = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    return p


def test_create_order_with_truck_and_date(auth_client, manager):
    c = Client.objects.create(first_name="И", last_name="П", phone="x")
    prod = _product()
    resp = auth_client(manager).post("/api/orders/", {
        "client": c.id, "truck_number": "01A777", "arrival_date": "2026-07-01",
        "items": [{"product": prod.id, "quantity": 5}],
    }, format="json")
    assert resp.status_code == 201
    o = Order.objects.get()
    assert o.truck_number == "01A777"
    assert str(o.arrival_date) == "2026-07-01"
    assert resp.data["truck_number"] == "01A777"
