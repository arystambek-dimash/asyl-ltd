import pytest
from decimal import Decimal

from apps.catalog.models import ClientPrice, Product
from apps.warehouse.models import StockItem
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.portal.serializers import MAX_PORTAL_ORDER_ITEMS, MAX_PORTAL_ITEM_QUANTITY

pytestmark = pytest.mark.django_db


def _product():
    p = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    return p


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
    assert order.settlement_intent == "pending"
    assert order.payment_method == "pending"


@pytest.mark.parametrize(
    "items",
    [
        [],
        lambda product_id: [
            {"product": product_id, "quantity": 1},
            {"product": product_id, "quantity": 2},
        ],
        lambda product_id: [
            {"product": product_id, "quantity": 1}
            for _ in range(MAX_PORTAL_ORDER_ITEMS + 1)
        ],
        lambda product_id: [
            {"product": product_id, "quantity": MAX_PORTAL_ITEM_QUANTITY + 1}
        ],
    ],
)
def test_portal_order_rejects_abusive_item_lists(
    auth_client, client_user, items
):
    _client_for(client_user)
    product = _product()
    payload_items = items(product.pk) if callable(items) else items

    response = auth_client(client_user).post(
        "/api/portal/orders/", {"items": payload_items}, format="json"
    )

    assert response.status_code == 400
    assert not Order.objects.exists()


@pytest.mark.parametrize(
    ("method", "intent"),
    [("invoice", "instant"), ("kaspi", "instant"),
     ("cash", "instant"), ("debt", "debt")],
)
def test_payment_method_sent_during_creation_is_deferred_until_shipment(
        auth_client, client_user, method, intent):
    _client_for(client_user)
    prod = _product()
    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": prod.id, "quantity": 1}],
         "payment_method": method},
        format="json",
    )
    assert resp.status_code == 201
    order = Order.objects.get()
    assert order.payment_method == "pending"
    assert order.settlement_intent == "pending"
    assert resp.data["payment_method"] == "pending"


def test_client_can_choose_instant_intent(auth_client, client_user):
    _client_for(client_user)
    prod = _product()
    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": prod.id, "quantity": 1}], "settlement_intent": "instant"},
        format="json",
    )
    assert resp.status_code == 201
    order = Order.objects.get()
    assert order.settlement_intent == "pending"
    assert order.payment_method == "pending"


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
    # Товар без складской карточки виден в каталоге с остатком 0
    # (но заказать его нельзя — проверяется отдельно).
    active = Product.objects.create(
        name="БезСклада", color="Blue", weight_kg="50", price="100.00")
    inactive = Product.objects.create(
        name="Скрытый", color="Green", weight_kg="50", price="100.00", is_active=False
    )

    resp = auth_client(client_user).get("/api/portal/catalog/")

    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.data}
    assert active.id in by_id
    assert inactive.id not in by_id
    assert by_id[active.id]["available_bags"] == 0
    assert by_id[active.id]["price"] is None


def test_client_catalog_returns_only_own_personal_price(
        auth_client, client_user, make_user):
    client = _client_for(client_user)
    other_user = make_user(username="priced-other", client=True)
    other = Client.objects.create(
        first_name="Другой", last_name="К", phone="2", user=other_user)
    product = _product()
    ClientPrice.objects.create(client=client, product=product, price="87.50")
    ClientPrice.objects.create(client=other, product=product, price="12.00")

    response = auth_client(client_user).get("/api/portal/catalog/")

    assert response.status_code == 200
    row = next(item for item in response.data if item["id"] == product.id)
    assert row["price"] == "87.50"


def test_portal_order_fixes_personal_price_at_creation(auth_client, client_user):
    client = _client_for(client_user)
    product = _product()
    ClientPrice.objects.create(client=client, product=product, price="91.25")

    response = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": product.id, "quantity": 2}]}, format="json",
    )

    assert response.status_code == 201
    assert OrderItem.objects.get(order_id=response.data["id"]).unit_price == Decimal("91.25")


def test_portal_client_selects_usd_price_and_order_currency(auth_client, client_user):
    client = _client_for(client_user)
    product = _product()
    ClientPrice.objects.create(
        client=client, product=product, currency="KZT", price="15000.00")
    ClientPrice.objects.create(
        client=client, product=product, currency="USD", price="31.25")

    catalog = auth_client(client_user).get(
        "/api/portal/catalog/", {"currency": "USD"})
    response = auth_client(client_user).post(
        "/api/portal/orders/",
        {"currency": "USD", "items": [{"product": product.id, "quantity": 2}]},
        format="json",
    )

    assert catalog.status_code == 200
    assert next(row for row in catalog.data if row["id"] == product.id)["price"] == "31.25"
    assert response.status_code == 201
    order = Order.objects.get(pk=response.data["id"])
    assert order.currency == "USD"
    assert order.items.get().unit_price == Decimal("31.25")


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
