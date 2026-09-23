"""Заказ «задним числом» и фиксация статуса с оплатой.

``backdate.date`` переносит дату заказа; статус и оплата проставляются только
явно (``status`` / ``paid``) — автоматически заказ не отгружается. Склад при
такой фиксации не списывается: такие заказы вносятся ради долгов и выручки,
остатки уже сверены вручную. Та же операция доступна для существующего
заказа через ``POST /orders/{id}/fixate/``.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, Payment
from apps.sales.models import Department
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db

BACKDATE_CODES = [
    "orders.view", "orders.create", "orders.edit", "orders.confirm",
    "payments.view", "payments.create",
]


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def backdater(user_with_perms):
    return user_with_perms("backdater", codes=BACKDATE_CODES)


@pytest.fixture
def setup():
    Department.objects.get_or_create(code="main", defaults={"name": "Основной"})
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    product = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    stock = StockItem.objects.create(product=product, bags=500)
    return client, product, stock


def _body(client, product, **backdate):
    return {
        "client": client.id,
        "department": "main",
        "items": [{"product": product.id, "quantity": 3}],
        "prices": {str(product.id): "15000"},
        "backdate": {"date": "2026-09-10", "status": "shipped", "paid": True,
                     "payment_method": "cash", **backdate},
    }


def _local_date(value):
    return timezone.localtime(value).date()


def test_backdated_order_is_shipped_and_paid_on_that_date(backdater, setup):
    client, product, stock = setup
    response = _api(backdater).post("/api/orders/", _body(client, product), format="json")
    assert response.status_code == 201, response.data

    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "shipped"
    assert order.payment_status == "settled"
    assert order.total_amount == Decimal("45000.00")
    assert _local_date(order.created_at) == date(2026, 9, 10)

    shipment = Shipment.objects.get(order=order)
    assert _local_date(shipment.shipped_at) == date(2026, 9, 10)
    assert shipment.bags_loaded == 3

    payment = Payment.objects.get(order=order)
    assert payment.status == "confirmed"
    assert payment.method == "cash"
    assert payment.amount == Decimal("45000.00")
    assert _local_date(payment.paid_at) == date(2026, 9, 10)
    assert _local_date(payment.received_at) == date(2026, 9, 10)
    assert _local_date(payment.confirmed_at) == date(2026, 9, 10)

    stock.refresh_from_db()
    assert stock.bags == 500, "склад не списывается для заказов задним числом"

    # Сводки по дням читают события: отгрузка и оплата тоже датированы задним числом.
    shipment_event = EventLog.objects.get(event_type="shipment", order=order)
    assert _local_date(shipment_event.created_at) == date(2026, 9, 10)
    for payment_event in EventLog.objects.filter(
        event_type="payment", order=order, payload__payment_id=payment.pk,
    ):
        assert _local_date(payment_event.created_at) == date(2026, 9, 10)
    # Само событие фиксации остаётся сегодняшним — это след аудита.
    event = EventLog.objects.get(event_type="order_backdated", order=order)
    assert _local_date(event.created_at) == timezone.localdate()
    assert event.payload["date"] == "2026-09-10"
    assert event.payload["status"] == "shipped"
    assert event.payload["paid"] is True

    assert response.data["status"] == "shipped"
    assert response.data["created_at"].startswith("2026-09-10")


def test_backdated_order_accepts_products_without_stock(backdater, setup):
    client, _, _ = setup
    product = Product.objects.create(name="Old", color="Blue", weight_kg="50")
    response = _api(backdater).post("/api/orders/", _body(client, product), format="json")
    assert response.status_code == 201, response.data
    assert Order.objects.get(pk=response.data["id"]).status == "shipped"


def test_backdated_shipped_but_unpaid_becomes_debt(backdater, setup):
    client, product, _ = setup
    response = _api(backdater).post(
        "/api/orders/", _body(client, product, paid=False), format="json",
    )
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "shipped"
    assert order.payment_status == "unpaid"
    assert not order.payments.exists()
    assert _local_date(order.created_at) == date(2026, 9, 10)


def test_backdated_confirmed_only_keeps_date(backdater, setup):
    client, product, _ = setup
    response = _api(backdater).post(
        "/api/orders/", _body(client, product, status="confirmed", paid=False), format="json",
    )
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "confirmed"
    assert not Shipment.objects.filter(order=order).exists()
    assert _local_date(order.created_at) == date(2026, 9, 10)


def test_backdate_date_only_keeps_normal_status(backdater, setup):
    client, product, _ = setup
    body = _body(client, product)
    body["backdate"] = {"date": "2026-09-10"}
    response = _api(backdater).post("/api/orders/", body, format="json")
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "confirmed"
    assert not order.payments.exists()
    assert _local_date(order.created_at) == date(2026, 9, 10)


def test_backdated_confirmed_order_can_be_prepaid(backdater, setup):
    client, product, _ = setup
    response = _api(backdater).post(
        "/api/orders/", _body(client, product, status="confirmed", paid=True), format="json",
    )
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "confirmed"
    assert order.payment_status == "settled"
    assert not Shipment.objects.filter(order=order).exists()
    payment = order.payments.get()
    assert payment.status == "confirmed"
    assert _local_date(payment.confirmed_at) == date(2026, 9, 10)


def test_backdated_paid_requires_confirmed_order(user_with_perms, setup):
    # Без права подтверждения заказ остаётся заявкой — оплату взять не за что.
    client, product, _ = setup
    no_confirm = user_with_perms(
        "no-confirm",
        codes=["orders.view", "orders.create", "orders.edit", "payments.view", "payments.create"],
    )
    response = _api(no_confirm).post(
        "/api/orders/", _body(client, product, status=None, paid=True), format="json",
    )
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_backdate_in_future_is_rejected(backdater, setup):
    client, product, _ = setup
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    response = _api(backdater).post(
        "/api/orders/", _body(client, product, date=tomorrow), format="json",
    )
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_backdate_requires_prices(backdater, setup):
    client, product, _ = setup
    body = _body(client, product)
    body.pop("prices")
    response = _api(backdater).post("/api/orders/", body, format="json")
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_backdate_requires_edit_and_payment_rights(user_with_perms, setup):
    client, product, _ = setup
    plain = user_with_perms("plain", codes=["orders.view", "orders.create", "orders.confirm"])
    response = _api(plain).post("/api/orders/", _body(client, product), format="json")
    assert response.status_code == 403
    assert not Order.objects.exists()

    no_payments = user_with_perms(
        "nopay", codes=["orders.view", "orders.create", "orders.confirm", "orders.edit"],
    )
    response = _api(no_payments).post("/api/orders/", _body(client, product), format="json")
    assert response.status_code == 403
    assert not Order.objects.exists()

    # Без оплаты право на кассу не требуется.
    response = _api(no_payments).post(
        "/api/orders/", _body(client, product, paid=False), format="json",
    )
    assert response.status_code == 201, response.data


def test_backdate_only_on_create(backdater, setup):
    client, product, _ = setup
    created = _api(backdater).post(
        "/api/orders/", _body(client, product, status="confirmed", paid=False), format="json",
    )
    order_id = created.data["id"]
    response = _api(backdater).patch(
        f"/api/orders/{order_id}/",
        {"backdate": {"date": "2026-09-01", "status": "shipped", "paid": True, "payment_method": "cash"}},
        format="json",
    )
    assert response.status_code == 400
    order = Order.objects.get(pk=order_id)
    assert order.status == "confirmed"
    assert _local_date(order.created_at) == date(2026, 9, 10)


def test_fixate_existing_order_status_and_payment(backdater, setup):
    client, product, stock = setup
    body = _body(client, product)
    body.pop("backdate")
    created = _api(backdater).post("/api/orders/", body, format="json")
    order_id = created.data["id"]
    assert Order.objects.get(pk=order_id).status == "confirmed"

    response = _api(backdater).post(
        f"/api/orders/{order_id}/fixate/",
        {"date": "2026-09-05", "status": "shipped", "paid": True, "payment_method": "kaspi"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["status"] == "shipped"
    order = Order.objects.get(pk=order_id)
    assert order.status == "shipped"
    assert order.payment_status == "settled"
    shipment = Shipment.objects.get(order=order)
    assert _local_date(shipment.shipped_at) == date(2026, 9, 5)
    payment = Payment.objects.get(order=order)
    assert payment.method == "kaspi"
    assert payment.status == "confirmed"
    assert _local_date(payment.confirmed_at) == date(2026, 9, 5)
    stock.refresh_from_db()
    assert stock.bags == 500
    # Дата создания уже существующего заказа не переписывается.
    assert _local_date(order.created_at) == timezone.localdate()


def test_fixate_rejects_already_shipped_and_wrong_rights(backdater, user_with_perms, setup):
    client, product, _ = setup
    created = _api(backdater).post("/api/orders/", _body(client, product), format="json")
    order_id = created.data["id"]
    response = _api(backdater).post(
        f"/api/orders/{order_id}/fixate/",
        {"date": "2026-09-05", "status": "shipped", "paid": False},
        format="json",
    )
    assert response.status_code == 400

    plain = user_with_perms("plain2", codes=["orders.view", "orders.create", "orders.confirm"])
    body = _body(client, product)
    body.pop("backdate")
    other = _api(backdater).post("/api/orders/", body, format="json")
    response = _api(plain).post(
        f"/api/orders/{other.data['id']}/fixate/",
        {"date": "2026-09-05", "status": "shipped", "paid": False},
        format="json",
    )
    assert response.status_code == 403


def test_fixate_payment_for_already_shipped_order(backdater, setup):
    client, product, _ = setup
    created = _api(backdater).post(
        "/api/orders/", _body(client, product, paid=False), format="json",
    )
    order_id = created.data["id"]
    response = _api(backdater).post(
        f"/api/orders/{order_id}/fixate/",
        {"date": "2026-09-12", "paid": True, "payment_method": "remote"},
        format="json",
    )
    assert response.status_code == 200, response.data
    order = Order.objects.get(pk=order_id)
    assert order.payment_status == "settled"
    payment = Payment.objects.get(order=order)
    assert payment.method == "remote"
    assert _local_date(payment.confirmed_at) == date(2026, 9, 12)
    # Дата отгрузки прежняя — фиксировалась только оплата.
    assert _local_date(Shipment.objects.get(order=order).shipped_at) == date(2026, 9, 10)
