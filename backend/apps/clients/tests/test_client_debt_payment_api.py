"""POST /api/clients/{id}/debt-payment/ — «Внести оплату» по клиенту.

Распределение и проверки живут в apps/orders/debt_payments.py (тесты сервиса —
рядом с ним). Здесь — контракт API: право, область отдела, форма тела и
ответа, коды ошибок доходят до кассы.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _url(client):
    return f"/api/clients/{client.id}/debt-payment/"


def _body(**overrides):
    return {
        "amount": "350.00",
        "method": "cash",
        "currency": "KZT",
        "preview": True,
        **overrides,
    }


def _preview_body(**overrides):
    """Предпросмотр, как его шлёт касса: без способа — его выбирают после «Подтвердить»."""
    body = _body(**overrides)
    del body["method"]
    return body


def _debt_order(client, product, *, qty, days_ago):
    """Отгруженный заказ в долг на qty × 100 ₸, созданный days_ago дней назад."""
    order = Order.objects.create(client=client, status="shipped", settlement_intent="debt")
    OrderItem.objects.create(order=order, product=product, quantity=qty, unit_price="100.00")
    Order.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago),
    )
    return order


@pytest.fixture
def cashier(user_with_perms):
    return user_with_perms("debt-cashier", codes=["payments.create"])


@pytest.fixture
def debtor(make_product):
    """Клиент с двумя долгами: старый 300 ₸ и новый 200 ₸."""
    client = Client.objects.create_with_user(
        first_name="Должник", last_name="Тестов", phone="87010000001",
    )
    product = make_product()
    old = _debt_order(client, product, qty=3, days_ago=10)
    new = _debt_order(client, product, qty=2, days_ago=1)
    return client, old, new


def test_preview_returns_oldest_first_plan_and_writes_nothing(api_as, cashier, debtor):
    client, old, new = debtor

    response = api_as(cashier).post(_url(client), _preview_body(), format="json")

    assert response.status_code == 200
    data = response.json()
    assert data["currency"] == "KZT"
    assert data["method"] is None
    assert data["amount"] == "350.00"
    assert data["total_available"] == "500.00"
    assert data["skipped"] == []
    assert data["slices"] == [
        {
            "order_id": old.id,
            "shipped_at": None,
            "remaining_before": "300.00",
            "amount": "300.00",
            "remaining_after": "0.00",
            "closes": True,
            "payment_id": None,
        },
        {
            "order_id": new.id,
            "shipped_at": None,
            "remaining_before": "200.00",
            "amount": "50.00",
            "remaining_after": "150.00",
            "closes": False,
            "payment_id": None,
        },
    ]
    assert not Payment.objects.filter(order__client=client).exists()
    assert not EventLog.objects.filter(event_type="debt_payment").exists()


def test_preview_without_amount_offers_whole_debt(api_as, cashier, debtor):
    client, old, new = debtor

    response = api_as(cashier).post(_url(client), _preview_body(amount=None), format="json")

    assert response.status_code == 200
    data = response.json()
    assert data["amount"] == "500.00"
    assert [(row["order_id"], row["amount"], row["closes"]) for row in data["slices"]] == [
        (old.id, "300.00", True),
        (new.id, "200.00", True),
    ]


def test_payment_records_one_payment_per_order_and_one_event(api_as, cashier, debtor):
    client, old, new = debtor

    response = api_as(cashier).post(_url(client), _body(preview=False), format="json")

    assert response.status_code == 200
    payments = {p.order_id: p for p in Payment.objects.filter(order__client=client)}
    assert set(payments) == {old.id, new.id}
    assert [(row["order_id"], row["payment_id"]) for row in response.json()["slices"]] == [
        (old.id, payments[old.id].id),
        (new.id, payments[new.id].id),
    ]
    assert payments[old.id].amount == Decimal("300.00")
    assert payments[new.id].amount == Decimal("50.00")
    for payment in payments.values():
        assert payment.method == "cash"
        assert payment.status == "confirmed"
        assert payment.recorded_by_id == cashier.id
    # Одна общая пометка внесения на всех оплатах.
    assert payments[old.id].note == payments[new.id].note
    assert payments[old.id].note.startswith("Внесение оплаты")
    old.refresh_from_db()
    new.refresh_from_db()
    assert old.payment_status == "settled"
    assert new.payment_status == "partial"
    event = EventLog.objects.get(event_type="debt_payment")
    assert event.order_id is None
    assert event.user_id == cashier.id
    assert event.payload["client_id"] == client.id
    assert [row["payment_id"] for row in event.payload["slices"]] == [
        payments[old.id].id,
        payments[new.id].id,
    ]


def test_requires_payments_create(api_as, user_with_perms, debtor):
    client, _old, _new = debtor
    reporter = user_with_perms("debt-reporter", codes=["clients.view", "reports.view"])

    response = api_as(reporter).post(_url(client), _body(preview=False), format="json")

    assert response.status_code == 403
    assert not Payment.objects.filter(order__client=client).exists()


def test_client_of_other_department_is_not_found(
    api_as, user_with_perms, departments, make_product,
):
    mill, city = departments
    product = make_product()
    own = Client.objects.create_with_user(
        first_name="Свой", phone="87010000002", department=mill,
    )
    foreign = Client.objects.create_with_user(
        first_name="Чужой", phone="87010000003", department=city,
    )
    _debt_order(own, product, qty=1, days_ago=1)
    _debt_order(foreign, product, qty=1, days_ago=1)
    mill_cashier = user_with_perms("mill-cashier", codes=["payments.create"], department=mill)
    api = api_as(mill_cashier)

    own_response = api.post(_url(own), _preview_body(amount="100.00"), format="json")
    foreign_response = api.post(
        _url(foreign), _body(amount="100.00", preview=False), format="json",
    )

    assert own_response.status_code == 200
    assert foreign_response.status_code == 404
    assert not Payment.objects.filter(order__client=foreign).exists()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"amount": "0"}, "invalid_amount"),
        ({"amount": None}, "invalid_amount"),
        ({"method": None}, "debt_payment_method"),
        ({"method": "invoice"}, "debt_payment_method"),
        ({"currency": "USD", "method": "kaspi"}, "payment_kzt_only"),
        ({"currency": "USD"}, "no_debt"),
        ({"currency": "EUR"}, "bad_currency"),
        ({"preview": "true"}, "bad_preview"),
    ],
)
def test_validation_codes_reach_the_cashier(api_as, cashier, debtor, overrides, code):
    client, _old, _new = debtor

    response = api_as(cashier).post(
        _url(client), _body(**{"preview": False, **overrides}), format="json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == code
    assert not Payment.objects.filter(order__client=client).exists()
    assert not EventLog.objects.filter(event_type="debt_payment").exists()


def test_amount_over_debt_reports_max_amount(api_as, cashier, debtor):
    client, _old, _new = debtor

    response = api_as(cashier).post(_url(client), _preview_body(amount="600.00"), format="json")

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "amount_exceeds_debt"
    assert data["max_amount"] == "500.00"
    assert data["detail"].startswith("Максимум к оплате")


def test_debt_payment_can_be_dated_a_past_day(api_as, cashier, debtor):
    """Клиент заплатил вчера: доли и сводное событие ложатся вчерашним днём."""
    client, old, new = debtor
    yesterday = timezone.localdate() - timedelta(days=1)

    response = api_as(cashier).post(
        _url(client), _body(amount="350.00", preview=False, date=yesterday.isoformat()), format="json",
    )

    assert response.status_code == 200, response.json()
    payments = Payment.objects.filter(order__client=client)
    assert {timezone.localdate(p.confirmed_at) for p in payments} == {yesterday}
    assert {timezone.localdate(p.paid_at) for p in payments} == {yesterday}
    moved = EventLog.objects.filter(order__client=client, event_type="payment")
    assert {timezone.localdate(e.created_at) for e in moved} == {yesterday}
    summary = EventLog.objects.get(event_type="debt_payment", payload__client_id=client.pk)
    assert timezone.localdate(summary.created_at) == yesterday
    # Кто и когда внёс оплату прошлым днём — сегодняшняя запись на каждую долю.
    audit = EventLog.objects.filter(event_type="order_backdated", order__client=client)
    assert audit.count() == 2
    assert {timezone.localdate(e.created_at) for e in audit} == {timezone.localdate()}


def test_debt_payment_dated_before_a_newer_order_skips_it(api_as, cashier, debtor):
    """Оплата пятидневной давности: новый заказ (вчерашний) тогда ещё не существовал."""
    client, old, new = debtor
    before_new = (timezone.localdate() - timedelta(days=5)).isoformat()

    preview = api_as(cashier).post(_url(client), _preview_body(amount=None, date=before_new), format="json")
    assert preview.status_code == 200, preview.json()
    data = preview.json()
    # «Весь долг» на тот день — только старый заказ, новый в «Пропущены» с причиной.
    assert data["total_available"] == "300.00"
    assert [row["order_id"] for row in data["slices"]] == [old.id]
    assert [(row["order_id"], row["reason"]) for row in data["skipped"]] == [(new.id, "after_payment_day")]

    response = api_as(cashier).post(_url(client), _body(amount="350.00", preview=False, date=before_new), format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "amount_exceeds_debt"

    response = api_as(cashier).post(_url(client), _body(amount="300.00", preview=False, date=before_new), format="json")
    assert response.status_code == 200, response.json()


def test_debt_payment_date_in_future_is_refused(api_as, cashier, debtor):
    client, _, _ = debtor
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

    response = api_as(cashier).post(_url(client), _body(preview=False, date=tomorrow), format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "payment_date_in_future"
    assert not Payment.objects.filter(order__client=client).exists()
