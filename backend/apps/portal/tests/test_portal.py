from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.portal.serializers import MAX_PORTAL_ITEM_QUANTITY, MAX_PORTAL_ORDER_ITEMS
from apps.warehouse.models import StockItem, Warehouse

pytestmark = pytest.mark.django_db


def _product():
    p = Product.objects.create(name="Премиум", color="Red", weight_kg="50")
    StockItem.objects.create(product=p, bags=500)
    return p


def _make_default(warehouse):
    Warehouse.objects.filter(is_default=True).exclude(pk=warehouse.pk).update(
        is_default=False
    )
    Warehouse.objects.filter(pk=warehouse.pk).update(is_default=True)
    warehouse.refresh_from_db()


def test_client_creates_own_pending_order(auth_client, client_user, own_client):
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
    auth_client, client_user, own_client, items
):
    product = _product()
    payload_items = items(product.pk) if callable(items) else items

    response = auth_client(client_user).post(
        "/api/portal/orders/", {"items": payload_items}, format="json"
    )

    assert response.status_code == 400
    assert not Order.objects.exists()


@pytest.mark.parametrize(
    "choice",
    [{"payment_method": "invoice"}, {"payment_method": "kaspi"},
     {"payment_method": "cash"}, {"payment_method": "debt"},
     {"settlement_intent": "instant"}],
)
def test_payment_choice_sent_during_creation_is_deferred_until_shipment(
        auth_client, client_user, own_client, choice):
    prod = _product()
    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": prod.id, "quantity": 1}], **choice},
        format="json",
    )
    assert resp.status_code == 201
    order = Order.objects.get()
    assert order.payment_method == "pending"
    assert order.settlement_intent == "pending"
    assert resp.data["payment_method"] == "pending"


def test_client_sees_only_own_orders(auth_client, client_user, own_client, make_user):
    other_user = make_user(username="other", client=True)
    other = Client.objects.create_with_user(first_name="Чужой", last_name="К", phone="y", user=other_user)
    Order.objects.create(client=own_client, status="draft")
    Order.objects.create(client=other, status="draft")
    resp = auth_client(client_user).get("/api/portal/orders/")
    assert resp.status_code == 200
    assert len(resp.data) == 1


def test_client_orders_are_listed_newest_first(auth_client, client_user, own_client):
    oldest = Order.objects.create(client=own_client, status="draft")
    newest = Order.objects.create(client=own_client, status="draft")
    middle = Order.objects.create(client=own_client, status="draft")
    now = timezone.now()
    # Порядок задаёт дата создания, а не порядок строк в таблице.
    Order.all_objects.filter(pk=newest.pk).update(created_at=now)
    Order.all_objects.filter(pk=middle.pk).update(created_at=now - timedelta(days=1))
    Order.all_objects.filter(pk=oldest.pk).update(created_at=now - timedelta(days=2))

    resp = auth_client(client_user).get("/api/portal/orders/")

    assert resp.status_code == 200
    assert [row["id"] for row in resp.data] == [newest.pk, middle.pk, oldest.pk]


def test_portal_order_rejects_archived_product(auth_client, client_user, own_client):
    product = _product()
    Product.objects.filter(pk=product.pk).update(is_active=False)

    resp = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": product.id, "quantity": 1}]}, format="json",
    )

    assert resp.status_code == 400
    assert "Архивный товар нельзя добавлять в заказ" in str(resp.data)
    assert not Order.objects.exists()


def test_client_cannot_fetch_foreign_order(auth_client, client_user, own_client, make_user):
    other_user = make_user(username="other", client=True)
    other = Client.objects.create_with_user(first_name="Чужой", last_name="К", phone="y", user=other_user)
    foreign = Order.objects.create(client=other, status="draft")
    resp = auth_client(client_user).get(f"/api/portal/orders/{foreign.id}/")
    assert resp.status_code == 404


def test_staff_cannot_use_portal(auth_client, manager):
    resp = auth_client(manager).get("/api/portal/orders/")
    assert resp.status_code == 403


def test_client_catalog_lists_only_active_products_with_positive_default_stock(
    auth_client, client_user, own_client,
):
    main = Warehouse.objects.get(code="main")
    available = Product.objects.create(
        name="В наличии", color="Blue", weight_kg="50")
    StockItem.objects.create(product=available, warehouse=main, bags=5)
    no_stock = Product.objects.create(
        name="Без склада", color="Blue", weight_kg="50")
    zero_stock = Product.objects.create(
        name="Нулевой остаток", color="Blue", weight_kg="50")
    StockItem.objects.create(product=zero_stock, warehouse=main, bags=0)
    inactive = Product.objects.create(
        name="Скрытый", color="Green", weight_kg="50", is_active=False
    )
    StockItem.objects.create(product=inactive, warehouse=main, bags=7)

    resp = auth_client(client_user).get("/api/portal/catalog/")

    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.data}
    assert available.id in by_id
    assert no_stock.id not in by_id
    assert zero_stock.id not in by_id
    assert inactive.id not in by_id
    assert "available_bags" not in by_id[available.id]
    assert by_id[available.id]["price"] is None


def test_client_catalog_scopes_products_to_active_default_warehouse(
    auth_client, client_user, own_client,
):
    main = Warehouse.objects.get(code="main")
    secondary = Warehouse.objects.create(
        code="south", name="Южный склад", is_active=True,
    )
    main_product = Product.objects.create(
        name="Главный", color="Red", weight_kg="50",
    )
    secondary_product = Product.objects.create(
        name="Южный", color="Blue", weight_kg="50",
    )
    StockItem.objects.create(product=main_product, warehouse=main, bags=10)
    StockItem.objects.create(
        product=secondary_product, warehouse=secondary, bags=3,
    )
    _make_default(secondary)

    response = auth_client(client_user).get("/api/portal/catalog/")

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [secondary_product.pk]

    secondary.is_active = False
    secondary.save(update_fields=["is_active"])
    response = auth_client(client_user).get("/api/portal/catalog/")

    assert response.status_code == 200
    assert response.data == []


def test_portal_catalog_does_not_leak_exact_balance(auth_client, client_user, own_client):
    product = Product.objects.create(
        name="Скрытый остаток", color="Red", weight_kg="50",
    )
    StockItem.objects.create(product=product, bags=987_654)

    portal_response = auth_client(client_user).get("/api/portal/catalog/")

    assert portal_response.status_code == 200
    portal_row = next(
        item for item in portal_response.data if item["id"] == product.pk
    )
    assert set(portal_row) == {
        "id", "label", "weight_kg", "price", "currency", "photo_url",
    }
    assert 987_654 not in portal_row.values()


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/products/",
        "/api/stock/",
        "/api/orders/form-options/",
    ],
)
def test_client_role_cannot_bypass_portal_to_read_staff_stock_endpoints(
    auth_client, client_user, endpoint,
):
    response = auth_client(client_user).get(endpoint)

    assert response.status_code == 403


def test_client_flag_denies_stock_even_with_accidentally_assigned_permissions(
    auth_client, user_with_perms,
):
    user = user_with_perms(
        "client-with-staff-permissions",
        codes=["catalog.view", "warehouse.view", "orders.create"],
    )
    user.is_client = True
    user.save(update_fields=["is_client"])

    for endpoint in (
        "/api/products/",
        "/api/stock/",
        "/api/orders/form-options/",
    ):
        assert auth_client(user).get(endpoint).status_code == 403


def test_client_catalog_returns_only_own_personal_price(
        auth_client, client_user, own_client, make_user):
    other_user = make_user(username="priced-other", client=True)
    other = Client.objects.create_with_user(
        first_name="Другой", last_name="К", phone="2", user=other_user)
    product = _product()
    ClientPrice.objects.create(client=own_client, product=product, price="87.50")
    ClientPrice.objects.create(client=other, product=product, price="12.00")

    response = auth_client(client_user).get("/api/portal/catalog/")

    assert response.status_code == 200
    row = next(item for item in response.data if item["id"] == product.id)
    assert row["price"] == "87.50"


def test_portal_order_fixes_personal_price_at_creation(auth_client, client_user, own_client):
    product = _product()
    ClientPrice.objects.create(client=own_client, product=product, price="91.25")

    response = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": product.id, "quantity": 2}]}, format="json",
    )

    assert response.status_code == 201
    assert OrderItem.objects.get(order_id=response.data["id"]).unit_price == Decimal("91.25")


def test_portal_client_selects_usd_price_and_order_currency(auth_client, client_user, own_client):
    product = _product()
    ClientPrice.objects.create(
        client=own_client, product=product, currency="KZT", price="15000.00")
    ClientPrice.objects.create(
        client=own_client, product=product, currency="USD", price="31.25")

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


def test_client_pending_order_hides_amounts(auth_client, client_user, own_client):
    product = _product()
    order = Order.objects.create(client=own_client, status="pending")
    OrderItem.objects.create(order=order, product=product, quantity=2)

    resp = auth_client(client_user).get(f"/api/portal/orders/{order.id}/")

    assert resp.status_code == 200
    assert resp.data["total_amount"] is None
    assert resp.data["paid_total"] is None
    assert resp.data["remaining_amount"] is None


def test_client_confirmed_order_shows_amounts(auth_client, client_user, own_client):
    product = _product()
    order = Order.objects.create(client=own_client, status="confirmed")
    OrderItem.objects.create(order=order, product=product, quantity=2, unit_price="100.00")

    resp = auth_client(client_user).get(f"/api/portal/orders/{order.id}/")

    assert resp.status_code == 200
    assert resp.data["total_amount"] == "200.00"
    assert resp.data["paid_total"] == "0.00"
    assert resp.data["remaining_amount"] == "200.00"


def test_client_overpaid_order_shows_no_remaining(auth_client, client_user, own_client):
    """Переплата — не отрицательный остаток: он и свободная сумма — через orders/debt.py."""
    product = _product()
    order = Order.objects.create(client=own_client, status="shipped")
    OrderItem.objects.create(order=order, product=product, quantity=2, unit_price="100.00")
    Payment.objects.create(order=order, amount="250.00", method="cash", status="confirmed")

    resp = auth_client(client_user).get(f"/api/portal/orders/{order.id}/")

    assert resp.status_code == 200
    assert resp.data["paid_total"] == "250.00"
    assert resp.data["remaining_amount"] == "0.00"
    assert resp.data["available_amount"] == "0.00"


def test_portal_order_item_snapshot_matches_order_item_save(
    auth_client, client_user, own_client,
):
    product = _product()

    response = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": product.id, "quantity": 2}]}, format="json",
    )

    assert response.status_code == 201
    item = OrderItem.objects.get(order_id=response.data["id"])
    assert item.product_label_snapshot == str(product)
    assert item.product_cv_class_snapshot == product.cv_class
    assert item.product_weight_kg_snapshot == Decimal("50")
