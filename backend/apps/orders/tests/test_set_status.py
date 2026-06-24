import pytest
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _order():
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100")
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status="draft")
    OrderItem.objects.create(order=o, product=prod, quantity=5)
    return o


def _client(user):
    cl = APIClient()
    cl.force_authenticate(user)
    return cl


def test_set_status_changes_status(manager):
    o = _order()
    r = _client(manager).post(f"/api/orders/{o.id}/set-status/", {"status": "shipped"})
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "shipped"


def test_set_status_rejects_unknown(manager):
    o = _order()
    r = _client(manager).post(f"/api/orders/{o.id}/set-status/", {"status": "nonsense"})
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.status == "draft"


def test_set_status_without_edit_creates_request(operator):
    # Без orders.edit ручная смена не применяется сразу — создаётся запрос на одобрение.
    o = _order()
    r = _client(operator).post(f"/api/orders/{o.id}/set-status/", {"status": "shipped"})
    assert r.status_code == 202
    assert r.data["applied"] is False
    o.refresh_from_db()
    assert o.status == "draft"  # статус не изменился до одобрения
