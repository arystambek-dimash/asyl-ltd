import pytest
from catalog.models import Product
from clients.models import Client
from orders.models import Order

pytestmark = pytest.mark.django_db


def _product():
    return Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")


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
