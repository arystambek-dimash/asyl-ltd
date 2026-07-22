import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from apps.catalog.models import Product, ClientPrice
from apps.warehouse.models import StockItem
from apps.clients.models import Client
from apps.orders.models import Order
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_staff_create_with_prices_confirms_immediately(manager):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = _api(manager).post(
        "/api/orders/",
        {
            "client": c.id,
            "items": [{"product": p.id, "quantity": 3}],
            "prices": {str(p.id): "15000"},  # цена по товару
        },
        format="json",
    )
    assert r.status_code == 201
    o = Order.objects.get()
    assert o.status == "confirmed"
    assert o.total_amount == Decimal("45000.00")
    assert ClientPrice.objects.get(client=c, product=p).price == Decimal("15000.00")


def test_staff_create_without_prices_stays_draft(manager):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = _api(manager).post(
        "/api/orders/",
        {
            "client": c.id,
            "items": [{"product": p.id, "quantity": 1}],
        },
        format="json",
    )
    assert r.status_code == 201
    assert Order.objects.get().status == "draft"


def test_staff_reviews_template_then_creates_linked_order(manager):
    client = Client.objects.create(first_name="Нью", last_name="Сити", phone="x")
    product = Product.objects.create(name="Template P", color="Blue", weight_kg="50")
    StockItem.objects.create(product=product, bags=500)
    source = Order.objects.create(client=client, status="shipped", created_by=manager)

    response = _api(manager).post(
        "/api/orders/",
        {
            "client": client.id,
            "template_order": source.id,
            "items": [{"product": product.id, "quantity": 4}],
            "prices": {str(product.id): "700"},
        },
        format="json",
    )

    assert response.status_code == 201
    created = Order.objects.get(pk=response.data["id"])
    assert created.repeated_from == source
    event = EventLog.objects.get(event_type="order_repeat", order=created)
    assert event.payload == {
        "source_order_id": source.id,
        "new_order_id": created.id,
        "mode": "reviewed_template",
    }


def test_staff_can_create_usd_order_and_remember_usd_price(manager):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="USD P", color="Red", weight_kg="50")
    StockItem.objects.create(product=p, bags=500)
    r = _api(manager).post(
        "/api/orders/",
        {
            "client": c.id,
            "currency": "USD",
            "items": [{"product": p.id, "quantity": 2}],
            "prices": {str(p.id): "25.50"},
        },
        format="json",
    )

    assert r.status_code == 201
    order = Order.objects.get(pk=r.data["id"])
    assert order.currency == "USD"
    assert order.total_amount == Decimal("51.00")
    assert ClientPrice.objects.get(
        client=c, product=p, currency="USD"
    ).price == Decimal("25.50")


def test_failed_price_confirmation_leaves_no_orphan_order(manager):
    # Регресс: create() атомарен — упавшее подтверждение цен не должно
    # оставлять в базе заказ без цен.
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = _api(manager).post(
        "/api/orders/",
        {
            "client": c.id,
            "items": [{"product": p.id, "quantity": 3}],
            "prices": {str(p.id): "0"},  # недопустимая цена → price_required
        },
        format="json",
    )
    assert r.status_code == 400
    assert Order.objects.count() == 0


def test_zero_quantity_rejected(manager):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = _api(manager).post(
        "/api/orders/",
        {
            "client": c.id,
            "items": [{"product": p.id, "quantity": 0}],
        },
        format="json",
    )
    assert r.status_code == 400
    assert Order.objects.count() == 0
