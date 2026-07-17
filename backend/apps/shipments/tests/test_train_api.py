import pytest
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("loader", codes=["train.view", "train.load"])


def _api(user):
    c = APIClient(); c.force_authenticate(user); return c


def _train_order(boss, status="confirmed", qty=10, stock=100):
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    receive_stock(p, stock, boss)
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status=status, transport_type="train")
    OrderItem.objects.create(order=o, product=p, quantity=qty)
    return o


def test_train_action_flow(loader, boss):
    o = _train_order(boss, qty=10)
    api = _api(loader)
    assert api.post(f"/api/orders/{o.id}/train/", {"action": "start"}, format="json").status_code == 200
    assert api.post(f"/api/orders/{o.id}/train/", {"action": "count", "bags": 10}, format="json").status_code == 200
    r = api.post(f"/api/orders/{o.id}/train/", {"action": "finish"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "shipped"


def test_train_action_requires_perm(accountant, boss):
    o = _train_order(boss)
    r = _api(accountant).post(f"/api/orders/{o.id}/train/", {"action": "start"}, format="json")
    assert r.status_code == 403


def test_train_bad_action_400(loader, boss):
    o = _train_order(boss)
    r = _api(loader).post(f"/api/orders/{o.id}/train/", {"action": "nope"}, format="json")
    assert r.status_code == 400


@pytest.mark.parametrize("bags", ["not-a-number", -1, None])
def test_train_count_rejects_invalid_bags(loader, boss, bags):
    o = _train_order(boss)
    api = _api(loader)
    assert api.post(
        f"/api/orders/{o.id}/train/", {"action": "start"}, format="json"
    ).status_code == 200

    response = api.post(
        f"/api/orders/{o.id}/train/",
        {"action": "count", "bags": bags}, format="json")

    assert response.status_code == 400
