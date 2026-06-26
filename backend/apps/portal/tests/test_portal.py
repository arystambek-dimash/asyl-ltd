import pytest
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

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
    assert order.settlement_intent == "debt"  # по умолчанию


def test_client_can_choose_instant_intent(auth_client, client_user):
    _client_for(client_user)
    prod = _product()
    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": prod.id, "quantity": 1}], "settlement_intent": "instant"},
        format="json",
    )
    assert resp.status_code == 201
    assert Order.objects.get().settlement_intent == "instant"


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


def test_client_catalog_lists_active_products_without_stock(auth_client, client_user):
    _client_for(client_user)
    active = _product()
    inactive = Product.objects.create(
        name="Скрытый", color="Green", weight_kg="50", price="100.00", is_active=False
    )

    resp = auth_client(client_user).get("/api/portal/catalog/")

    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.data}
    assert active.id in by_id
    assert inactive.id not in by_id
    assert by_id[active.id]["available_bags"] == 0


def test_client_order_without_profile_returns_400(auth_client, client_user):
    product = _product()

    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": product.id, "quantity": 1}]},
        format="json",
    )

    assert resp.status_code == 400
    assert resp.data["detail"] == "К аккаунту не привязан профиль клиента."


def test_client_pending_order_hides_amounts(auth_client, client_user):
    client = _client_for(client_user)
    product = _product()
    order = Order.objects.create(client=client, status="pending")
    OrderItem.objects.create(order=order, product=product, quantity=2)

    resp = auth_client(client_user).get(f"/api/portal/orders/{order.id}/")

    assert resp.status_code == 200
    assert resp.data["total_amount"] is None
    assert resp.data["paid_total"] is None
    assert resp.data["remaining_amount"] is None


def test_client_confirmed_order_shows_amounts(auth_client, client_user):
    client = _client_for(client_user)
    product = _product()
    order = Order.objects.create(client=client, status="confirmed")
    OrderItem.objects.create(order=order, product=product, quantity=2, unit_price="100.00")

    resp = auth_client(client_user).get(f"/api/portal/orders/{order.id}/")

    assert resp.status_code == 200
    assert resp.data["total_amount"] == "200.00"
    assert resp.data["paid_total"] == "0.00"
    assert resp.data["remaining_amount"] == "200.00"
