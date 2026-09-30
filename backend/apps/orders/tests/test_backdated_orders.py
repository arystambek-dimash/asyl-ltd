"""Заказ «задним числом» и фиксация статуса с оплатой.

``backdate.date`` переносит дату заказа; статус и оплата проставляются только
явно (``status`` / ``paid``) — автоматически заказ не отгружается. Склад при
такой фиксации не списывается: такие заказы вносятся ради долгов и выручки,
остатки уже сверены вручную. Отгрузка помечена ``Shipment.stock_deducted =
False``, поэтому откат и правка такого заказа склад тоже не трогают. Та же
операция доступна для существующего заказа через ``POST /orders/{id}/fixate/``.
"""
import importlib
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.backdate import backdate_moment
from apps.orders.models import Order, Payment
from apps.sales.models import Department
from apps.shipments.models import Shipment, ShipmentSource
from apps.shipments.services import dispatch_order, rollback_shipment
from apps.warehouse.models import StockItem, StockMovement
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db

BACKDATE_CODES = [
    "orders.view", "orders.create", "orders.edit", "orders.confirm",
    "payments.view", "payments.create",
]


@pytest.fixture
def backdater(user_with_perms):
    return user_with_perms("backdater", codes=BACKDATE_CODES)


@pytest.fixture
def setup():
    Department.objects.get_or_create(code="main", defaults={"name": "Основной"})
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    product = Product.objects.create(name="P", color="Red", weight_kg="50")
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


def test_backdated_order_is_shipped_and_paid_on_that_date(backdater, setup, api_as):
    client, product, stock = setup
    response = api_as(backdater).post("/api/orders/", _body(client, product), format="json")
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


def test_backdated_order_accepts_products_without_stock(backdater, setup, api_as):
    client, _, _ = setup
    product = Product.objects.create(name="Old", color="Blue", weight_kg="50")
    response = api_as(backdater).post("/api/orders/", _body(client, product), format="json")
    assert response.status_code == 201, response.data
    assert Order.objects.get(pk=response.data["id"]).status == "shipped"


def test_backdated_shipped_but_unpaid_becomes_debt(backdater, setup, api_as):
    client, product, _ = setup
    response = api_as(backdater).post(
        "/api/orders/", _body(client, product, paid=False), format="json",
    )
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "shipped"
    assert order.payment_status == "unpaid"
    assert not order.payments.exists()
    assert _local_date(order.created_at) == date(2026, 9, 10)
    # Как у обычной отгрузки: остаток — долг, событие датировано днём отгрузки.
    debt_event = EventLog.objects.get(event_type="debt", order=order)
    assert debt_event.payload["amount"] == "45000.00"
    assert _local_date(debt_event.created_at) == date(2026, 9, 10)


def test_backdated_confirmed_only_keeps_date(backdater, setup, api_as):
    client, product, _ = setup
    response = api_as(backdater).post(
        "/api/orders/", _body(client, product, status="confirmed", paid=False), format="json",
    )
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "confirmed"
    assert not Shipment.objects.filter(order=order).exists()
    assert _local_date(order.created_at) == date(2026, 9, 10)


def test_backdate_date_only_keeps_normal_status(backdater, setup, api_as):
    client, product, _ = setup
    body = _body(client, product)
    body["backdate"] = {"date": "2026-09-10"}
    response = api_as(backdater).post("/api/orders/", body, format="json")
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "confirmed"
    assert not order.payments.exists()
    assert _local_date(order.created_at) == date(2026, 9, 10)


def test_backdated_confirmed_order_can_be_prepaid(backdater, setup, api_as):
    client, product, _ = setup
    response = api_as(backdater).post(
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


def test_backdated_paid_requires_confirmed_order(user_with_perms, setup, api_as):
    # Без права подтверждения заказ остаётся заявкой — оплату взять не за что.
    client, product, _ = setup
    no_confirm = user_with_perms(
        "no-confirm",
        codes=["orders.view", "orders.create", "orders.edit", "payments.view", "payments.create"],
    )
    response = api_as(no_confirm).post(
        "/api/orders/", _body(client, product, status=None, paid=True), format="json",
    )
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_backdate_in_future_is_rejected(backdater, setup, api_as):
    client, product, _ = setup
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    response = api_as(backdater).post(
        "/api/orders/", _body(client, product, date=tomorrow), format="json",
    )
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_backdate_requires_prices(backdater, setup, api_as):
    client, product, _ = setup
    body = _body(client, product)
    body.pop("prices")
    response = api_as(backdater).post("/api/orders/", body, format="json")
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_backdate_requires_edit_and_payment_rights(user_with_perms, setup, api_as):
    client, product, _ = setup
    plain = user_with_perms("plain", codes=["orders.view", "orders.create", "orders.confirm"])
    response = api_as(plain).post("/api/orders/", _body(client, product), format="json")
    assert response.status_code == 403
    assert not Order.objects.exists()

    no_payments = user_with_perms(
        "nopay", codes=["orders.view", "orders.create", "orders.confirm", "orders.edit"],
    )
    response = api_as(no_payments).post("/api/orders/", _body(client, product), format="json")
    assert response.status_code == 403
    assert not Order.objects.exists()

    # Без оплаты право на кассу не требуется.
    response = api_as(no_payments).post(
        "/api/orders/", _body(client, product, paid=False), format="json",
    )
    assert response.status_code == 201, response.data


def test_backdate_only_on_create(backdater, setup, api_as):
    client, product, _ = setup
    created = api_as(backdater).post(
        "/api/orders/", _body(client, product, status="confirmed", paid=False), format="json",
    )
    order_id = created.data["id"]
    response = api_as(backdater).patch(
        f"/api/orders/{order_id}/",
        {"backdate": {"date": "2026-09-01", "status": "shipped", "paid": True, "payment_method": "cash"}},
        format="json",
    )
    assert response.status_code == 400
    order = Order.objects.get(pk=order_id)
    assert order.status == "confirmed"
    assert _local_date(order.created_at) == date(2026, 9, 10)


def test_fixate_existing_order_status_and_payment(backdater, setup, api_as):
    client, product, stock = setup
    body = _body(client, product)
    body.pop("backdate")
    created = api_as(backdater).post("/api/orders/", body, format="json")
    order_id = created.data["id"]
    assert Order.objects.get(pk=order_id).status == "confirmed"

    response = api_as(backdater).post(
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


def test_fixate_rejects_already_shipped_and_wrong_rights(backdater, user_with_perms, setup, api_as):
    client, product, _ = setup
    created = api_as(backdater).post("/api/orders/", _body(client, product), format="json")
    order_id = created.data["id"]
    response = api_as(backdater).post(
        f"/api/orders/{order_id}/fixate/",
        {"date": "2026-09-05", "status": "shipped", "paid": False},
        format="json",
    )
    assert response.status_code == 400

    plain = user_with_perms("plain2", codes=["orders.view", "orders.create", "orders.confirm"])
    body = _body(client, product)
    body.pop("backdate")
    other = api_as(backdater).post("/api/orders/", body, format="json")
    response = api_as(plain).post(
        f"/api/orders/{other.data['id']}/fixate/",
        {"date": "2026-09-05", "status": "shipped", "paid": False},
        format="json",
    )
    assert response.status_code == 403


def test_fixate_payment_for_already_shipped_order(backdater, setup, api_as):
    client, product, _ = setup
    created = api_as(backdater).post(
        "/api/orders/", _body(client, product, paid=False), format="json",
    )
    order_id = created.data["id"]
    response = api_as(backdater).post(
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


def _fixated(backdater, setup, api_as):
    """Отгружен задним числом без оплаты: 3 мешка, склад не списан."""
    client, product, _ = setup
    response = api_as(backdater).post(
        "/api/orders/", _body(client, product, paid=False), format="json",
    )
    assert response.status_code == 201, response.data
    return Order.objects.get(pk=response.data["id"])


def test_fixation_marks_shipment_stock_not_deducted(backdater, setup, api_as):
    client, product, _ = setup
    created = _fixated(backdater, setup, api_as)
    assert Shipment.objects.get(order=created).stock_deducted is False

    body = _body(client, product)
    body.pop("backdate")
    confirmed = api_as(backdater).post("/api/orders/", body, format="json")
    response = api_as(backdater).post(
        f"/api/orders/{confirmed.data['id']}/fixate/",
        {"date": "2026-09-05", "status": "shipped", "paid": False},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert Shipment.objects.get(order_id=confirmed.data["id"]).stock_deducted is False
    # Строки «с какого склада» пишет только отгрузка, которая списывает склад.
    assert not ShipmentSource.objects.exists()


def test_rollback_of_fixation_restores_no_stock(backdater, boss, setup, api_as):
    _, product, stock = setup
    order = _fixated(backdater, setup, api_as)

    rollback_shipment(order, boss, target_status="confirmed", reason="Фиксация по ошибке")

    stock.refresh_from_db()
    assert stock.bags == 500, "склад не списывали — возвращать нечего"
    assert not StockMovement.objects.filter(product=product).exists()
    assert not Shipment.objects.filter(order=order).exists()
    event = EventLog.objects.get(event_type="shipment_rollback", order=order)
    assert event.payload["stock_basis"] == "not_deducted"
    assert event.payload["restored"] == []
    assert event.payload["restored_bags"] == 0


def test_edit_of_fixated_shipped_order_moves_no_stock(backdater, setup, api_as):
    _, product, stock = setup
    order = _fixated(backdater, setup, api_as)

    response = api_as(backdater).patch(
        f"/api/orders/{order.pk}/",
        {
            "items": [{"product": product.id, "quantity": 5}],
            "prices": {str(product.id): "15000"},
            "edit_reason": "Уточнили количество мешков",
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert order.items.get().quantity == 5
    stock.refresh_from_db()
    assert stock.bags == 500
    assert not StockMovement.objects.filter(product=product).exists()
    assert not ShipmentSource.objects.filter(shipment__order=order).exists()
    assert Shipment.objects.get(order=order).stock_deducted is False
    event = EventLog.objects.get(event_type="order_edit", order=order)
    assert event.payload["action"] == "shipment_correction"
    assert event.payload["stock_changes"] == []
    assert event.payload["sources_after"] is None


def test_reship_after_fixation_rollback_deducts_and_restores_normally(backdater, boss, setup, api_as):
    _, product, stock = setup
    order = _fixated(backdater, setup, api_as)
    rollback_shipment(order, boss, target_status="confirmed", reason="Отгрузим по-настоящему")

    dispatch_order(order, boss)

    stock.refresh_from_db()
    assert stock.bags == 497
    shipment = Shipment.objects.get(order=order)
    assert shipment.stock_deducted is True, "новая отгрузка — новый Shipment, склад списан"
    assert list(shipment.sources.values_list("product_id", "warehouse_id", "bags")) == [
        (product.id, stock.warehouse_id, 3),
    ]
    assert list(
        StockMovement.objects.filter(product=product).values_list("reason", "delta")
    ) == [("shipment", -3)]

    rollback_shipment(order, boss, target_status="confirmed", reason="Вернули мешки на склад")

    stock.refresh_from_db()
    assert stock.bags == 500
    event = (
        EventLog.objects.filter(event_type="shipment_rollback", order=order)
        .order_by("-id")
        .first()
    )
    assert event.payload["stock_basis"] == "recorded"
    assert event.payload["restored_bags"] == 3
    assert event.payload["restored"] == [{
        "product": product.id,
        "warehouse": stock.warehouse_id,
        "warehouse_name": stock.warehouse.name,
        "bags": 3,
    }]


BACKFILL_MIGRATION = "apps.shipments.migrations.0019_backfill_fixation_stock_deducted"
_FIXATION = {"source": "fixation", "stock_deducted": False}


def _shipment(client, *, status="shipped"):
    order = Order.objects.create(client=client, status=status)
    return Shipment.objects.create(order=order, bags_loaded=3, shipped_at=timezone.now())


def _event(shipment, event_type, *, backdated=False, **payload):
    event = EventLog.objects.create(
        event_type=event_type, message=event_type, order_id=shipment.order_id, payload=payload,
    )
    if backdated:
        # Как у фиксации: дата события уезжает назад, а id остаётся свежим.
        EventLog.objects.filter(pk=event.pk).update(created_at=backdate_moment(date(2026, 9, 1)))
    return event


def test_backfill_marks_only_uncorrected_fixations(setup, capsys):
    client, product, _ = setup
    migration = importlib.import_module(BACKFILL_MIGRATION)

    fixated = _shipment(client)
    _event(fixated, "shipment", backdated=True, **_FIXATION)

    trashed = _shipment(client)
    _event(trashed, "shipment", backdated=True, **_FIXATION)
    Order.all_objects.filter(pk=trashed.order_id).update(deleted_at=timezone.now())

    # Отгружен, откачен и зафиксирован: по created_at последней была бы обычная отгрузка.
    refixated = _shipment(client)
    _event(refixated, "shipment", bags_loaded=3)
    _event(refixated, "shipment", backdated=True, **_FIXATION)

    # Зафиксирован, откачен и отгружен обычным путём: склад списан.
    reshipped = _shipment(client)
    _event(reshipped, "shipment", backdated=True, **_FIXATION)
    _event(reshipped, "shipment", bags_loaded=3)

    # Правка после фиксации уже сдвинула склад старым кодом — только ручная сверка.
    corrected = _shipment(client)
    _event(corrected, "shipment", backdated=True, **_FIXATION)
    _event(corrected, "order_edit", action="shipment_correction")

    # Правка была раньше фиксации (отгрузка → правка → откат → фиксация).
    corrected_before = _shipment(client)
    _event(corrected_before, "order_edit", action="shipment_correction")
    _event(corrected_before, "shipment", backdated=True, **_FIXATION)

    # Фиксацию откатили, заказ снова на посту: новый Shipment ещё спишет склад.
    arrived = _shipment(client, status="arrived")
    _event(arrived, "shipment", backdated=True, **_FIXATION)

    # Строки источников пишет только отгрузка со списанием.
    recorded = _shipment(client)
    ShipmentSource.objects.create(
        shipment=recorded, product=product, warehouse=get_default_warehouse(), bags=3,
    )
    _event(recorded, "shipment", backdated=True, **_FIXATION)

    silent = _shipment(client)

    migration.backfill_fixation_stock_deducted(django_apps, None)

    assert dict(Shipment.objects.values_list("order_id", "stock_deducted")) == {
        fixated.order_id: False,
        trashed.order_id: False,
        refixated.order_id: False,
        reshipped.order_id: True,
        corrected.order_id: True,
        corrected_before.order_id: False,
        arrived.order_id: True,
        recorded.order_id: True,
        silent.order_id: True,
    }
    assert re.findall(r"#(\d+)", capsys.readouterr().out) == [str(corrected.order_id)]
