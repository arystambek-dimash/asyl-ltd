from decimal import Decimal

import pytest
from apps.catalog.models import Product
from apps.warehouse.models import StockItem
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _product():
    p = Product.objects.create(name="Премиум", color="Red", weight_kg="50")
    StockItem.objects.create(product=p, bags=500)
    return p


def test_order_status_not_settable_via_create(auth_client, manager):
    client = Client.objects.create_with_user(first_name="L", last_name="К", phone="x")
    prod = _product()
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "status": "shipped",
         "items": [{"product": prod.id, "quantity": 1}]},
        format="json",
    )
    assert resp.status_code == 201
    assert Order.objects.get().status == "draft"


def test_unknown_transport_type_is_rejected(auth_client, manager):
    client = Client.objects.create_with_user(first_name="L", last_name="К", phone="x")
    prod = _product()
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "transport_type": "plane",
         "items": [{"product": prod.id, "quantity": 1}]},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.parametrize("body", [{"items": []}, {"prices": []}, {"prices": "100"}])
def test_invalid_order_shape_is_rejected_without_writes(auth_client, manager, body):
    client = Client.objects.create_with_user(first_name="L", last_name="К", phone="x")
    prod = _product()
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "items": [{"product": prod.id, "quantity": 1}], **body},
        format="json",
    )
    assert resp.status_code == 400
    assert not Order.objects.exists()


def test_archived_product_with_stock_cannot_be_ordered(auth_client, manager):
    client = Client.objects.create_with_user(first_name="L", last_name="К", phone="x")
    prod = _product()
    Product.objects.filter(pk=prod.pk).update(is_active=False)
    resp = auth_client(manager).post(
        "/api/orders/",
        {"client": client.id, "items": [{"product": prod.id, "quantity": 1}]},
        format="json",
    )
    assert resp.status_code == 400
    assert not Order.objects.exists()


def test_order_total_can_exceed_a_single_unit_price_field(auth_client, manager):
    client = Client.objects.create_with_user(first_name="L", last_name="К", phone="x")
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(order=order, quantity=3, unit_price=Decimal("9999999999.99"))
    resp = auth_client(manager).get(f"/api/orders/{order.id}/")
    assert resp.status_code == 200
    assert resp.data["total_amount"] == "29999999999.97"
