import pytest
from catalog.models import Product
from clients.models import Client
from orders.models import Order

pytestmark = pytest.mark.django_db


def _product(price="100.00"):
    return Product.objects.create(name="Премиум", color="Red", weight_kg="50", price=price)


def test_manager_creates_order_with_items(auth_client, manager):
    client = Client.objects.create(first_name="Лидер", last_name="К", phone="x")
    prod = _product("100.00")
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "items": [{"product": prod.id, "quantity": 5}]},
        format="json",
    )
    assert resp.status_code == 201
    order = Order.objects.get()
    assert order.status == "draft"
    assert order.total_amount == 500


def test_order_status_not_settable_via_create(auth_client, manager):
    client = Client.objects.create(first_name="L", last_name="К", phone="x")
    prod = _product()
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "status": "shipped",
         "items": [{"product": prod.id, "quantity": 1}]},
        format="json",
    )
    assert resp.status_code == 201
    assert Order.objects.get().status == "draft"
