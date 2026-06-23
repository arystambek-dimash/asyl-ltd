import pytest
from catalog.models import Product
from clients.models import Client
from orders.models import Order

pytestmark = pytest.mark.django_db


def _product():
    return Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")


def _client_for(user):
    return Client.objects.create(first_name="Мой", last_name="К", phone="x", user=user)


def test_client_creates_own_pending_order(auth_client, client_user):
    _client_for(client_user)
    prod = _product()
    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": prod.id, "quantity": 3}]}, format="json",
    )
    assert resp.status_code == 201
    order = Order.objects.get()
    assert order.status == "pending"
    assert order.client.user_id == client_user.id


def test_client_sees_only_own_orders(auth_client, client_user, make_user):
    mine = _client_for(client_user)
    other_user = make_user(username="other", client=True)
    other = Client.objects.create(first_name="Чужой", last_name="К", phone="y", user=other_user)
    Order.objects.create(client=mine, status="draft")
    Order.objects.create(client=other, status="draft")
    resp = auth_client(client_user).get("/api/portal/orders/")
    assert resp.status_code == 200
    assert len(resp.data) == 1


def test_client_cannot_fetch_foreign_order(auth_client, client_user, make_user):
    _client_for(client_user)
    other_user = make_user(username="other", client=True)
    other = Client.objects.create(first_name="Чужой", last_name="К", phone="y", user=other_user)
    foreign = Order.objects.create(client=other, status="draft")
    resp = auth_client(client_user).get(f"/api/portal/orders/{foreign.id}/")
    assert resp.status_code == 404


def test_staff_cannot_use_portal(auth_client, manager):
    resp = auth_client(manager).get("/api/portal/orders/")
    assert resp.status_code == 403
