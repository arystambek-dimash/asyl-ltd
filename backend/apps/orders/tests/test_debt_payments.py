"""«Внести оплату» по клиенту: сумма гасит долговые заказы от старого к новому."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.eventlog.models import EventLog
from apps.orders import debt_payments
from apps.orders.debt_payments import plan_debt_payment, record_client_debt_payment
from apps.orders.models import Order, OrderItem, Payment
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


@pytest.fixture
def cashier(user_with_perms, departments):
    return user_with_perms("mill-cashier", codes=["payments.create"], department=departments[0])


def _client(department, *, currency="KZT"):
    return Client.objects.create_with_user(
        first_name="Клиент", phone="+7 (705) 565-65-65", department=department, currency=currency,
    )


def _debt(client, amount, *, currency="KZT", shipped=None, created=None, **fields):
    """Отгруженный неоплаченный заказ на ``amount``: одна позиция, один мешок."""
    order = Order.objects.create(
        client=client, status="shipped", currency=currency, department=client.department.code, **fields,
    )
    product, _ = Product.objects.get_or_create(name="Мука", color="White", weight_kg=Decimal("50"))
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=Decimal(amount))
    if created is not None:
        Order.objects.filter(pk=order.pk).update(created_at=created)
    if shipped is not None:
        Shipment.objects.create(order=order, shipped_at=shipped)
    return order


def _pay(client, amount, user, **options):
    options.setdefault("method", "cash")
    options.setdefault("currency", "KZT")
    return record_client_debt_payment(client, amount, user, **options)


def test_pays_oldest_first_and_leaves_the_last_order_partial(cashier, departments):
    client = _client(departments[0])
    now = timezone.now()
    first = _debt(client, "1000.00", shipped=now - timedelta(days=3))
    second = _debt(client, "500.00", shipped=now - timedelta(days=2))
    third = _debt(client, "800.00", shipped=now - timedelta(days=1))

    result = _pay(client, "1800.00", cashier)

    assert result["amount"] == "1800.00"
    assert result["total_available"] == "2300.00"
    assert result["skipped"] == []
    assert [
        (row["order_id"], row["remaining_before"], row["amount"], row["remaining_after"], row["closes"])
        for row in result["slices"]
    ] == [
        (first.pk, "1000.00", "1000.00", "0.00", True),
        (second.pk, "500.00", "500.00", "0.00", True),
        (third.pk, "800.00", "300.00", "500.00", False),
    ]
    statuses = dict(Order.objects.filter(client=client).values_list("pk", "payment_status"))
    assert statuses == {first.pk: "settled", second.pk: "settled", third.pk: "partial"}


def test_oldest_by_shipment_date_backdated_and_rail_orders(cashier, departments):
    client = _client(departments[0])
    now = timezone.now()
    created_first_shipped_last = _debt(
        client, "100.00", created=now - timedelta(days=20), shipped=now - timedelta(days=1),
    )
    backdated_shipment = now - timedelta(days=10)
    backdated = _debt(client, "100.00", shipped=backdated_shipment)
    without_shipment = _debt(client, "100.00", created=now - timedelta(days=5))
    rail = _debt(
        client, "100.00", shipped=now - timedelta(days=7), transport_type="train", rail_station="Ст. Раустан",
    )

    result = _pay(client, None, cashier, preview=True)

    assert [row["order_id"] for row in result["slices"]] == [
        backdated.pk, rail.pk, without_shipment.pk, created_first_shipped_last.pk,
    ]
    assert result["slices"][0]["shipped_at"] == timezone.localtime(backdated_shipment).isoformat()
    assert result["slices"][2]["shipped_at"] is None


def test_reserved_money_reduces_the_share_and_a_fully_reserved_order_is_skipped(cashier, departments):
    client = _client(departments[0])
    now = timezone.now()
    busy = _debt(client, "300.00", shipped=now - timedelta(days=3))
    Payment.objects.create(order=busy, amount=Decimal("300.00"), method="invoice", status="requested")
    reserved = _debt(client, "500.00", shipped=now - timedelta(days=2))
    Payment.objects.create(order=reserved, amount=Decimal("200.00"), method="cash", status="received")
    last = _debt(client, "400.00", shipped=now - timedelta(days=1))

    result = _pay(client, None, cashier, preview=True)

    assert result["amount"] == "700.00"
    assert result["total_available"] == "700.00"
    assert result["skipped"] == [
        {"order_id": busy.pk, "reason": "payment_in_progress", "detail": "оплата уже в процессе"},
    ]
    assert [
        (row["order_id"], row["remaining_before"], row["amount"], row["remaining_after"], row["closes"])
        for row in result["slices"]
    ] == [
        (reserved.pk, "500.00", "300.00", "200.00", False),
        (last.pk, "400.00", "400.00", "0.00", True),
    ]


def test_store_order_outside_its_payment_day_is_skipped(cashier, departments):
    client = _client(departments[0])
    store = Store.objects.create(client=client, name="Магазин", payment_schedule_type="monthly", payment_days=[5])
    now = timezone.now()
    store_order = _debt(client, "300.00", shipped=now - timedelta(days=2), store=store)
    plain = _debt(client, "400.00", shipped=now - timedelta(days=1))

    with patch("apps.orders.debt_payments.timezone.localdate", return_value=date(2026, 6, 6)):
        result = _pay(client, "400.00", cashier)

    assert result["skipped"] == [
        {"order_id": store_order.pk, "reason": "payment_window_closed", "detail": "не день оплаты по графику магазина"},
    ]
    assert result["total_available"] == "400.00"
    assert [row["order_id"] for row in result["slices"]] == [plain.pk]
    assert not Payment.objects.filter(order=store_order).exists()


def test_plan_follows_the_store_schedule_for_the_given_day(departments):
    client = _client(departments[0])
    store = Store.objects.create(client=client, name="Магазин", payment_schedule_type="monthly", payment_days=[5])
    order = _debt(client, "300.00", store=store)
    orders = list(
        Order.objects.filter(pk=order.pk).select_related("store", "shipment").prefetch_related("payments", "items")
    )

    closed = plan_debt_payment(orders, None, today=date(2026, 6, 6))
    opened = plan_debt_payment(orders, Decimal("100.00"), today=date(2026, 6, 5))

    assert closed["amount"] == closed["total_available"] == Decimal("0")
    assert closed["slices"] == []
    assert [row["reason"] for row in closed["skipped"]] == ["payment_window_closed"]
    assert opened["amount"] == Decimal("100.00")
    assert opened["total_available"] == Decimal("300.00")
    assert opened["skipped"] == []
    assert opened["slices"] == [{
        "order": order,
        "amount": Decimal("100.00"),
        "remaining_before": Decimal("300.00"),
        "remaining_after": Decimal("200.00"),
        "closes": False,
    }]


def test_usd_debt_is_paid_in_cash_only(cashier, departments):
    client = _client(departments[0], currency="USD")
    _debt(client, "100.00", currency="USD")

    with pytest.raises(ValidationError) as error:
        _pay(client, "50.00", cashier, method="kaspi", currency="USD")

    assert error.value.detail["code"] == "payment_kzt_only"
    assert not Payment.objects.exists()


@pytest.mark.parametrize("method", ["invoice", "card", "debt", None])
def test_only_money_already_at_the_till_can_be_paid_in(cashier, departments, method):
    client = _client(departments[0])
    _debt(client, "100.00")

    with pytest.raises(ValidationError) as error:
        _pay(client, "50.00", cashier, method=method)

    assert error.value.detail["code"] == "debt_payment_method"


@pytest.mark.parametrize("amount", [None, "", "0", "-5", "abc", "1.001"])
def test_amount_must_be_positive_money(cashier, departments, amount):
    client = _client(departments[0])
    _debt(client, "100.00")

    with pytest.raises(ValidationError) as error:
        _pay(client, amount, cashier)

    assert error.value.detail["code"] == "invalid_amount"


def test_only_the_chosen_currency_is_paid(cashier, departments):
    client = _client(departments[0])
    kzt = _debt(client, "1000.00")
    usd = _debt(client, "100.00", currency="USD")

    result = _pay(client, "100.00", cashier, currency="USD")

    assert result["currency"] == "USD"
    assert [row["order_id"] for row in result["slices"]] == [usd.pk]
    assert not Payment.objects.filter(order=kzt).exists()
    usd.refresh_from_db()
    assert usd.payment_status == "settled"


def test_client_without_debt_in_the_currency(cashier, departments):
    client = _client(departments[0])
    _debt(client, "100.00")

    with pytest.raises(ValidationError) as error:
        _pay(client, None, cashier, currency="USD", preview=True)

    assert error.value.detail["code"] == "no_debt"
    assert error.value.detail["detail"] == "У клиента нет долга в USD"


def test_trashed_order_is_never_paid(cashier, departments):
    client = _client(departments[0])
    now = timezone.now()
    trashed = _debt(client, "500.00", shipped=now - timedelta(days=5))
    Order.all_objects.filter(pk=trashed.pk).update(deleted_at=now)
    live = _debt(client, "300.00", shipped=now - timedelta(days=1))

    result = _pay(client, "300.00", cashier)

    assert result["total_available"] == "300.00"
    assert [row["order_id"] for row in result["slices"]] == [live.pk]
    assert not Payment.objects.filter(order_id=trashed.pk).exists()


def test_amount_above_the_debt_is_rejected_with_the_maximum(cashier, departments):
    client = _client(departments[0])
    _debt(client, "1000.00")
    _debt(client, "250.00")

    with pytest.raises(ValidationError) as error:
        _pay(client, "1250.01", cashier)

    assert error.value.detail["code"] == "amount_exceeds_debt"
    assert error.value.detail["detail"] == "Максимум к оплате 1 250 ₸"
    assert error.value.detail["max_amount"] == "1250.00"
    assert not Payment.objects.exists()


def test_preview_writes_nothing(cashier, departments):
    client = _client(departments[0])
    order = _debt(client, "1000.00")
    payments_before = Payment.objects.count()
    events_before = EventLog.objects.count()

    result = _pay(client, "400.00", cashier, preview=True)

    assert result["slices"] == [{
        "order_id": order.pk,
        "shipped_at": None,
        "remaining_before": "1000.00",
        "amount": "400.00",
        "remaining_after": "600.00",
        "closes": False,
        "payment_id": None,
    }]
    assert Payment.objects.count() == payments_before
    assert EventLog.objects.count() == events_before
    order.refresh_from_db()
    assert order.payment_status == "unpaid"


def test_preview_needs_no_method(cashier, departments):
    """Способ кассир выбирает после «Подтвердить»: разбивка от него не зависит."""
    client = _client(departments[0], currency="USD")
    order = _debt(client, "100.00", currency="USD")

    result = record_client_debt_payment(client, "40.00", cashier, currency="USD", preview=True)

    assert result["method"] is None
    assert [(row["order_id"], row["amount"]) for row in result["slices"]] == [(order.pk, "40.00")]
    assert not Payment.objects.exists()


def test_one_failing_share_rolls_back_the_whole_payment(cashier, departments, monkeypatch):
    client = _client(departments[0])
    now = timezone.now()
    _debt(client, "300.00", shipped=now - timedelta(days=2))
    _debt(client, "300.00", shipped=now - timedelta(days=1))
    real_record = debt_payments.record_staff_payment
    calls = []

    def fail_on_second_share(order, amount, user, **options):
        calls.append(order.pk)
        if len(calls) == 2:
            raise ValidationError({"detail": "Заказ больше недоступен", "code": "order_not_active"})
        return real_record(order, amount, user, **options)

    monkeypatch.setattr(debt_payments, "record_staff_payment", fail_on_second_share)

    with pytest.raises(ValidationError) as error:
        _pay(client, "600.00", cashier)

    assert error.value.detail["code"] == "order_not_active"
    assert len(calls) == 2
    assert not Payment.objects.filter(order__client=client).exists()
    assert not EventLog.objects.filter(event_type__in=["payment", "debt_payment"]).exists()


def test_shares_are_confirmed_payments_with_one_note_and_one_event(cashier, departments):
    client = _client(departments[0])
    now = timezone.now()
    first = _debt(client, "300.00", shipped=now - timedelta(days=3))
    second = _debt(client, "300.00", shipped=now - timedelta(days=2))
    third = _debt(client, "600.00", shipped=now - timedelta(days=1))

    result = _pay(client, "700.00", cashier)

    payments = list(Payment.objects.filter(order__client=client).order_by("order_id"))
    note = "Внесение оплаты: 700 ₸, наличные (3 заказа)"
    assert [(p.order_id, p.amount, p.method, p.status, p.note) for p in payments] == [
        (first.pk, Decimal("300.00"), "cash", "confirmed", note),
        (second.pk, Decimal("300.00"), "cash", "confirmed", note),
        (third.pk, Decimal("100.00"), "cash", "confirmed", note),
    ]
    assert [row["payment_id"] for row in result["slices"]] == [p.pk for p in payments]
    event = EventLog.objects.get(event_type="debt_payment")
    assert event.order is None
    assert event.user == cashier
    assert event.message == "Внесение оплаты клиента «Клиент»: 700 ₸, наличные (3 заказа)"
    assert event.payload == {
        "client_id": client.pk,
        "department": "mill",
        "currency": "KZT",
        "method": "cash",
        "amount": "700.00",
        "slices": [
            {"order_id": first.pk, "payment_id": payments[0].pk, "amount": "300.00"},
            {"order_id": second.pk, "payment_id": payments[1].pk, "amount": "300.00"},
            {"order_id": third.pk, "payment_id": payments[2].pk, "amount": "100.00"},
        ],
    }


def test_other_department_cannot_pay_in(user_with_perms, departments):
    mill, city = departments
    client = _client(mill)
    _debt(client, "300.00")
    stranger = user_with_perms("city-cashier", codes=["payments.create"], department=city)

    with pytest.raises(PermissionDenied):
        _pay(client, "300.00", stranger)

    assert not Payment.objects.filter(order__client=client).exists()
    assert not EventLog.objects.filter(event_type="debt_payment").exists()
