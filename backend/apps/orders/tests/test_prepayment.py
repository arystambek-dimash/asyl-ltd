"""Предоплата: касса принимает деньги до отгрузки (встреча 23.09, трек P).

Оплата по-прежнему никогда не блокирует логистику — меняется только то, когда
можно принять деньги. До отгрузки сотрудник берёт только деньги, которые уже у
кассы (наличные, свой Kaspi-терминал, «удалённо»); Kaspi QR, счёт на телефон и
портал клиента ждут отгрузки.
"""

from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.eventlog.models import EventLog
from apps.orders import services
from apps.orders.refunds import create_cash_refund
from apps.orders.debt import order_overpaid
from apps.orders.fixation import fixate_order
from apps.orders.models import Order, OrderItem, Payment, StatusChangeRequest
from apps.orders.statuses import AWAITING_SHIPMENT_STATUSES, is_payment_open
from apps.shipments.models import Shipment
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db

SETTLED_METHODS = ("cash", "kaspi", "remote")
NOT_SHIPPED = ("draft", "pending", "confirmed", "arrived", "loading", "loaded", "rejected", "cancelled")


@pytest.fixture
def product(boss):
    item = Product.objects.create(name="Мука 1с", color="White", weight_kg="50")
    receive_stock(item, 1000, boss)
    return item


def _order(product, status="confirmed", quantity=10, price="1000.00", client=None, **fields):
    client = client or Client.objects.create_with_user(first_name="Клиент", phone="+7 (701) 111-22-33")
    order = Order.objects.create(client=client, status=status, **fields)
    OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price=Decimal(price))
    return order


def _prepay(order, user, amount, method="cash"):
    return services.record_staff_payment(order, amount, user, method=method)


# ── P1: когда касса может принять деньги ───────────────────────────────────


@pytest.mark.parametrize("method", [*SETTLED_METHODS, "invoice", None])
def test_shipped_order_is_open_for_every_staff_method(method):
    assert is_payment_open("shipped", method=method) is True


@pytest.mark.parametrize("status", AWAITING_SHIPMENT_STATUSES)
@pytest.mark.parametrize("method", SETTLED_METHODS)
def test_staff_takes_prepayment_with_money_already_at_the_till(status, method):
    assert is_payment_open(status, method=method) is True


@pytest.mark.parametrize("status", AWAITING_SHIPMENT_STATUSES)
@pytest.mark.parametrize("method", ["invoice", None])
def test_requests_for_money_wait_for_shipment(status, method):
    # Счёт на телефон и Kaspi QR (запрос денег, method=None) — только после отгрузки.
    assert is_payment_open(status, method=method) is False


@pytest.mark.parametrize("status", ["draft", "pending", "rejected", "cancelled"])
@pytest.mark.parametrize("method", [*SETTLED_METHODS, "invoice"])
def test_unconfirmed_or_closed_orders_take_no_money(status, method):
    assert is_payment_open(status, method=method) is False


@pytest.mark.parametrize("status", NOT_SHIPPED)
@pytest.mark.parametrize("method", [*SETTLED_METHODS, "invoice"])
def test_client_pays_only_after_shipment(status, method):
    assert is_payment_open(status, method=method, by_client=True) is False
    assert is_payment_open("shipped", method=method, by_client=True) is True


@pytest.mark.parametrize("status", AWAITING_SHIPMENT_STATUSES)
@pytest.mark.parametrize("method", SETTLED_METHODS)
def test_cashier_records_prepayment_before_shipment(auth_client, accountant, product, status, method):
    order = _order(product, status=status)

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/", {"amount": "4000.00", "method": method}, format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["status"] == "confirmed"
    order.refresh_from_db()
    assert order.status == status
    assert order.payment_status == "partial"


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        ("draft", "Оплата доступна после подтверждения заказа"),
        ("pending", "Оплата доступна после подтверждения заказа"),
        ("rejected", "Заказ отменён — оплата не принимается"),
        ("cancelled", "Заказ отменён — оплата не принимается"),
    ],
)
def test_cashier_cannot_take_money_for_unconfirmed_or_closed_order(auth_client, accountant, product, status, detail):
    order = _order(product, status=status)

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/", {"amount": "4000.00", "method": "cash"}, format="json"
    )

    assert response.status_code == 400
    assert response.data["code"] == "payment_not_open"
    # Окно модалки показывает причину сервера: «после отгрузки» тут неправда.
    assert response.data["detail"] == detail
    assert not order.payments.exists()


def test_invoice_before_shipment_is_closed(auth_client, accountant, product):
    order = _order(product)
    body = {"amount": "4000.00", "method": "invoice", "phone_number": "+77011112233"}

    with patch("apps.orders.views.create_invoice") as create_invoice:
        response = auth_client(accountant).post(f"/api/orders/{order.id}/payments/", body, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "payment_not_open"
    create_invoice.assert_not_called()
    assert not order.payments.exists()


def test_kaspi_qr_before_shipment_is_closed(auth_client, accountant, product):
    # Поздняя оплата старого QR на отменённом заказе увела бы деньги из учёта.
    order = _order(product)

    with patch("apps.orders.views.create_invoice") as create_invoice:
        response = auth_client(accountant).post(
            f"/api/orders/{order.id}/payments/",
            {"amount": "4000", "method": "kaspi", "channel": "qr"},
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "payment_not_open"
    create_invoice.assert_not_called()
    assert not order.payments.exists()


@pytest.mark.parametrize("method", ["cash", "kaspi", "invoice"])
def test_client_portal_payment_stays_closed_before_shipment(product, make_user, method):
    order = _order(product)
    client_user = make_user(username="portal", client=True)

    with pytest.raises(ValidationError) as exc:
        services.create_client_payment(order, method, client_user)

    assert exc.value.detail["code"] == "payment_not_open"


def _store_order(product, status):
    client = Client.objects.create_with_user(first_name="Магазин", phone="+7 (701) 222-33-44")
    store = Store.objects.create(client=client, name="Точка", payment_schedule_type="monthly", payment_days=[5])
    return _order(product, status=status, client=client, store=store)


def test_store_payment_window_does_not_block_prepayment(boss, product):
    order = _store_order(product, "confirmed")
    with patch("apps.orders.services.timezone.localdate", return_value=date(2026, 6, 6)):
        payment = _prepay(order, boss, "1000.00")
    assert payment.status == "confirmed"


def test_store_payment_window_applies_after_shipment(boss, product):
    order = _store_order(product, "shipped")
    with patch("apps.orders.services.timezone.localdate", return_value=date(2026, 6, 6)):
        with pytest.raises(ValidationError) as exc:
            _prepay(order, boss, "1000.00")
    assert exc.value.detail["code"] == "payment_window_closed"


# ── P2: окно оплаты считает сервер ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "is_open", "methods", "request_open"),
    [
        ("pending", False, [], False),
        ("confirmed", True, ["cash", "kaspi", "remote"], False),
        ("loaded", True, ["cash", "kaspi", "remote"], False),
        ("shipped", True, ["cash", "kaspi", "remote", "invoice"], True),
        ("cancelled", False, [], False),
    ],
)
def test_order_api_reports_payment_window(auth_client, accountant, product, status, is_open, methods, request_open):
    order = _order(product, status=status)

    data = auth_client(accountant).get(f"/api/orders/{order.id}/").data

    assert data["payment_open"] is is_open
    assert data["payment_open_methods"] == methods
    # «kaspi» в списке — свой терминал; Kaspi QR и счёт на телефон (запрос денег)
    # открывает отдельный признак: кнопке POS не нужно повторять правило статусов.
    assert data["payment_request_open"] is request_open
    assert data["overpaid_amount"] == "0.00"


@pytest.mark.parametrize("status", ["confirmed", "shipped"])
def test_usd_order_api_opens_only_cash(auth_client, accountant, product, status):
    order = _order(product, status=status, currency="USD")

    data = auth_client(accountant).get(f"/api/orders/{order.id}/").data

    assert data["payment_open_methods"] == ["cash"]
    assert data["payment_request_open"] is False


@pytest.mark.parametrize("method", ["kaspi", "remote", "invoice"])
def test_usd_order_accepts_only_cash(auth_client, accountant, product, method):
    order = _order(product, status="shipped", currency="USD")

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/", {"amount": "100", "method": method}, format="json"
    )

    assert response.status_code == 400
    assert response.data["code"] == "payment_kzt_only"
    assert not order.payments.exists()
    assert _prepay(order, accountant, "100.00").status == "confirmed"


# ── P3: отгрузка не затирает предоплату ────────────────────────────────────


def _ship(order, user):
    from apps.shipments.services import manual_complete_order

    manual_complete_order(order, None, user)
    order.refresh_from_db()
    return order


def test_shipping_fully_prepaid_order_keeps_it_settled(boss, product):
    order = _order(product)
    _prepay(order, boss, "10000.00")

    order = _ship(order, boss)

    assert order.status == "shipped"
    assert order.payment_status == "settled"
    assert order.is_debt is False
    assert not EventLog.objects.filter(order=order, event_type="debt").exists()


def test_shipping_partly_prepaid_order_logs_debt_on_remainder(boss, product):
    order = _order(product)
    _prepay(order, boss, "4000.00")

    order = _ship(order, boss)

    assert order.payment_status == "partial"
    event = EventLog.objects.get(order=order, event_type="debt")
    assert event.payload["amount"] == "6000.00"
    assert "6000" in event.message


def test_backdated_shipment_keeps_prepayment(boss, user_with_perms, product):
    fixer = user_with_perms("fixer", codes=["orders.edit", "payments.create", "orders.view"])
    order = _order(product)
    _prepay(order, boss, "10000.00")

    order = fixate_order(order, fixer, date=date(2026, 9, 10), status="shipped")

    assert order.status == "shipped"
    assert order.payment_status == "settled"


def test_backdated_payment_is_allowed_for_confirmed_order(user_with_perms, product):
    fixer = user_with_perms("fixer", codes=["orders.edit", "payments.create", "orders.view"])
    order = _order(product)

    order = fixate_order(order, fixer, date=date(2026, 9, 10), paid=True)

    assert order.status == "confirmed"
    assert order.payment_status == "settled"
    assert order.payments.get().confirmed_at.date().isoformat() == "2026-09-10"


def test_backdated_payment_stays_closed_for_pending_order(user_with_perms, product):
    fixer = user_with_perms("fixer", codes=["orders.edit", "payments.create", "orders.view"])
    order = _order(product, status="pending")

    with pytest.raises(ValidationError) as exc:
        fixate_order(order, fixer, date=date(2026, 9, 10), paid=True)

    assert exc.value.detail["code"] == "payment_not_open"


# ── P4: деньги не пропадают вместе с заказом ───────────────────────────────


@pytest.mark.parametrize("target", ["cancelled", "pending"])
def test_order_with_money_cannot_be_cancelled_until_refunded(auth_client, accountant, product, target):
    order = _order(product)
    payment = _prepay(order, accountant, "4000.00")
    api = auth_client(accountant)

    blocked = api.post(f"/api/orders/{order.id}/set-status/", {"status": target}, format="json")

    assert blocked.status_code == 400
    assert blocked.data["code"] == "order_has_payments"
    assert "4000.00 KZT" in blocked.data["detail"]
    order.refresh_from_db()
    assert order.status == "confirmed"

    create_cash_refund(payment, accountant, reason="Клиент отказался")
    allowed = api.post(f"/api/orders/{order.id}/set-status/", {"status": target}, format="json")
    assert allowed.status_code == 200, allowed.data
    order.refresh_from_db()
    assert order.status == target


def test_payment_in_progress_also_blocks_cancellation(boss, product):
    order = _order(product)
    Payment.objects.create(order=order, amount="1000.00", method="cash", status="received")

    with pytest.raises(ValidationError) as exc:
        services._force_set_status(order, "cancelled", boss)

    assert exc.value.detail["code"] == "order_has_payments"


def test_status_request_is_refused_upfront_when_order_has_money(auth_client, user_with_perms, boss, product):
    requester = user_with_perms("requester", codes=["orders.view"])
    order = _order(product)
    _prepay(order, boss, "4000.00")

    response = auth_client(requester).post(
        f"/api/orders/{order.id}/set-status/", {"status": "cancelled"}, format="json"
    )

    assert response.status_code == 400
    assert response.data["code"] == "order_has_payments"
    assert not StatusChangeRequest.objects.filter(order=order).exists()


def test_status_request_for_shipped_order_names_the_real_reason(auth_client, user_with_perms, boss, product):
    # Возврат денег отгруженному не поможет: откат отгрузки — отдельная операция.
    requester = user_with_perms("requester", codes=["orders.view"])
    order = _ship(_order(product), boss)
    _prepay(order, boss, "4000.00")

    response = auth_client(requester).post(
        f"/api/orders/{order.id}/set-status/", {"status": "cancelled"}, format="json"
    )

    assert response.status_code == 400
    assert response.data["code"] == "shipped_is_final"
    assert not StatusChangeRequest.objects.filter(order=order).exists()


def test_approving_old_cancel_request_is_refused_after_prepayment(boss, product):
    order = _order(product)
    request = StatusChangeRequest.objects.create(order=order, to_status="cancelled", requested_by=boss)
    _prepay(order, boss, "4000.00")

    with pytest.raises(ValidationError) as exc:
        services.approve_status_change(request, boss)

    assert exc.value.detail["code"] == "order_has_payments"
    request.refresh_from_db()
    assert request.status == "pending"


def test_rewind_with_money_goes_back_to_waiting_but_not_to_cancelled(boss, product):
    from apps.shipments.services import rewind_loading

    order = _order(product, status="arrived")
    _prepay(order, boss, "4000.00")

    with pytest.raises(ValidationError) as exc:
        rewind_loading(order, boss, target_status="cancelled")
    assert exc.value.detail["code"] == "order_has_payments"

    order = rewind_loading(order, boss, target_status="confirmed")
    assert order.status == "confirmed"


def test_reject_is_blocked_only_by_money(boss, product):
    with_money = _order(product, status="pending")
    Payment.objects.create(order=with_money, amount="1000.00", method="cash", status="requested")
    with pytest.raises(ValidationError) as exc:
        services.reject_order(with_money, boss, reason="Нет товара")
    assert exc.value.detail["code"] == "order_has_payments"

    # Отклонённая оплата — история, а не деньги: заявку можно отклонить.
    without_money = _order(product, status="pending")
    Payment.objects.create(order=without_money, amount="1000.00", method="cash", status="rejected")
    services.reject_order(without_money, boss, reason="Нет товара")
    without_money.refresh_from_db()
    assert without_money.status == "rejected"


def test_unshipped_order_with_money_cannot_be_deleted(auth_client, accountant, product):
    order = _order(product)
    _prepay(order, accountant, "4000.00")

    response = auth_client(accountant).delete(f"/api/orders/{order.id}/")

    assert response.status_code == 400
    assert response.data["code"] == "order_has_payments"
    assert Order.objects.filter(pk=order.pk).exists()


def test_shipped_order_with_money_can_still_be_deleted(boss, accountant, product):
    from apps.clients.reports.statements.data import build_statement_data

    order = _order(product, status="shipped")
    _prepay(order, boss, "4000.00")

    services.soft_delete_order(order, boss)

    assert not Order.objects.filter(pk=order.pk).exists()
    # Корзина — это удалённое: ни журнал кассы, ни выписка заказ и его деньги
    # не видят.
    journal = APIClient()
    journal.force_authenticate(accountant)
    assert journal.get("/api/payment-transactions/").data["results"] == []
    statement = build_statement_data(client=order.client)
    assert list(statement.payments) == []
    assert list(statement.operations) == []


def test_shipment_rollback_keeps_prepayment_when_order_waits_again(boss, product):
    from apps.shipments.services import loader_rollback_blocker, rollback_shipment

    order = _order(product)
    _prepay(order, boss, "10000.00")
    order = _ship(order, boss)

    with pytest.raises(ValidationError) as exc:
        rollback_shipment(order, boss, target_status="cancelled", reason="Ошибочная отгрузка")
    assert exc.value.detail["code"] == "order_has_payments"

    rollback_shipment(order, boss, target_status="confirmed", reason="Ошибочная отгрузка")
    order.refresh_from_db()
    assert order.status == "confirmed"
    # Деньги остаются предоплатой — статус оплаты не сбрасывается.
    assert order.payment_status == "settled"
    assert order.paid_total == Decimal("10000.00")

    # Грузчику предоплата тоже не мешает отменить свою свежую отгрузку.
    order = _ship(order, boss)
    EventLog.objects.filter(order=order, event_type="shipment").update(user=boss)
    assert loader_rollback_blocker(order, boss) == ""


# ── P1: восстановление и повторная выдача счёта — то же окно оплаты ────────


def _rejected(order, method, *, received=True):
    return Payment.objects.create(
        order=order,
        amount="1000.00",
        method=method,
        status="rejected",
        received_at=timezone.now() if received else None,
    )


def test_rejected_payment_cannot_come_back_as_money_on_cancelled_order(auth_client, accountant, product):
    order = _order(product)
    payment = _rejected(order, "cash")
    # Отклонённая оплата — не деньги: отмена заказа её не ждёт.
    services._force_set_status(order, "cancelled", accountant)

    response = auth_client(accountant).post(f"/api/payment-transactions/{payment.id}/restore/")

    assert response.status_code == 400
    assert response.data["code"] == "payment_not_open"
    payment.refresh_from_db()
    assert payment.status == "rejected"


def test_restored_invoice_before_shipment_is_refused_without_provider_call(auth_client, accountant, product):
    order = _order(product)
    payment = _rejected(order, "invoice", received=False)

    with patch("apps.orders.views.create_invoice") as create_invoice:
        response = auth_client(accountant).post(f"/api/payment-transactions/{payment.id}/restore/")

    assert response.status_code == 400
    assert response.data["code"] == "payment_not_open"
    create_invoice.assert_not_called()
    payment.refresh_from_db()
    assert payment.status == "rejected"


def test_restored_cash_before_shipment_is_prepayment(auth_client, accountant, product):
    order = _order(product)
    payment = _rejected(order, "cash")

    response = auth_client(accountant).post(f"/api/payment-transactions/{payment.id}/restore/")

    assert response.status_code == 200, response.data
    payment.refresh_from_db()
    assert payment.status == "received"


def test_new_provider_invoice_is_not_reissued_after_rollback_to_waiting(auth_client, accountant, boss, product):
    from apps.shipments.services import rollback_shipment

    order = _ship(_order(product), boss)
    # Счёт на телефон создан, а выдать его провайдеру не удалось — «Повторно выдать».
    payment = Payment.objects.create(order=order, amount="1000.00", method="invoice", status="requested")
    rollback_shipment(order, boss, target_status="confirmed", reason="Ошибочная отгрузка")

    with patch("apps.orders.views.create_invoice") as create_invoice:
        response = auth_client(accountant).post(f"/api/payment-transactions/{payment.id}/issue/")

    assert response.status_code == 400
    assert response.data["code"] == "payment_not_open"
    create_invoice.assert_not_called()


# ── P5: переплата — «К возврату» ───────────────────────────────────────────


def test_reducing_prepaid_order_is_allowed_and_shows_overpayment(auth_client, accountant, product):
    order = _order(product)
    _prepay(order, accountant, "10000.00")
    api = auth_client(accountant)

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"items": [{"product": product.pk, "quantity": 6}], "prices": {str(product.pk): "1000.00"}},
        format="json",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order_overpaid(order) == Decimal("4000.00")
    assert order.payment_status == "settled"
    assert api.get(f"/api/orders/{order.id}/").data["overpaid_amount"] == "4000.00"


def test_price_correction_below_confirmed_money_is_allowed(boss, product):
    order = _order(product)
    _prepay(order, boss, "10000.00")

    services.correct_order_prices(order, boss, total_amount="8000.00")

    order.refresh_from_db()
    assert order_overpaid(order) == Decimal("2000.00")


def test_price_correction_still_guards_active_payment_requests(boss, product):
    order = _order(product, status="shipped")
    Payment.objects.create(order=order, amount="9000.00", method="cash", status="received")

    with pytest.raises(ValidationError) as exc:
        services.correct_order_prices(order, boss, total_amount="8000.00")

    assert exc.value.detail["code"] == "active_payments_exceed_total"


@pytest.fixture
def cashier(user_with_perms, departments):
    return user_with_perms(
        "mill-cashier",
        codes=["payments.confirm", "payments.create"],
        department=departments[0],
    )


def _department_order(product, department, **fields):
    client = Client.objects.create_with_user(
        first_name=f"Клиент {department.code}", phone="+7 (705) 565-65-65", department=department
    )
    return _order(product, client=client, department=department.code, **fields)


def _ids(response):
    assert response.status_code == 200, response.data
    rows = response.data["results"] if isinstance(response.data, dict) else response.data
    return [row["id"] for row in rows]


def test_to_refund_lists_overpaid_orders_of_cashier_department(auth_client, cashier, boss, departments, product):
    mill, city = departments
    overpaid = _department_order(product, mill)
    _prepay(overpaid, boss, "10000.00")
    services.correct_order_prices(overpaid, boss, total_amount="7000.00")
    exact = _department_order(product, mill)
    _prepay(exact, boss, "10000.00")
    foreign = _department_order(product, city)
    _prepay(foreign, boss, "10000.00")
    services.correct_order_prices(foreign, boss, total_amount="7000.00")
    usd = _department_order(product, mill, currency="USD")
    _prepay(usd, boss, "10000.00")
    services.correct_order_prices(usd, boss, total_amount="9000.00")
    api = auth_client(cashier)

    assert set(_ids(api.get("/api/orders/to-refund/"))) == {overpaid.pk, usd.pk}
    summary = api.get("/api/orders/to-refund/", {"summary": "1"}).data
    assert summary == [
        {"currency": "KZT", "amount": "3000.00", "count": 1},
        {"currency": "USD", "amount": "1000.00", "count": 1},
    ]

    payment = overpaid.payments.get()
    refunded = api.post(
        f"/api/payment-transactions/{payment.pk}/refund/",
        {"mode": "cash", "amount": "3000.00", "reason": "Переплата после правки заказа"},
        format="json",
    )
    assert refunded.status_code == 201, refunded.data
    assert set(_ids(api.get("/api/orders/to-refund/"))) == {usd.pk}


def test_refund_in_progress_is_not_offered_again(auth_client, cashier, boss, departments, product):
    order = _department_order(product, departments[0])
    payment = _prepay(order, boss, "10000.00")
    services.correct_order_prices(order, boss, total_amount="7000.00")
    api = auth_client(cashier)

    def overpaid():
        return order_overpaid(Order.objects.get(pk=order.pk))

    # Возврат по ссылке покупателю (Kaspi QR / ApiPay) ждёт его подтверждения:
    # начатая часть второй раз к возврату не предлагается.
    Payment.objects.filter(pk=payment.pk).update(pending_refund_amount="2000.00")
    assert overpaid() == Decimal("1000.00")
    assert auth_client(boss).get(f"/api/orders/{order.id}/").data["overpaid_amount"] == "1000.00"
    assert api.get("/api/orders/to-refund/", {"summary": "1"}).data == [
        {"currency": "KZT", "amount": "1000.00", "count": 1}
    ]

    Payment.objects.filter(pk=payment.pk).update(pending_refund_amount="3000.00")
    assert overpaid() == Decimal("0")
    assert _ids(api.get("/api/orders/to-refund/")) == []


@pytest.mark.parametrize("status", ["cancelled", "rejected"])
def test_money_on_order_outside_turnover_is_all_to_refund(
    auth_client, cashier, accountant, departments, product, status
):
    # Легаси до предоплаты или поздняя оплата старого QR: заказ вне оборота
    # (statuses.is_financial) ничего не стоит — к возврату все его деньги,
    # даже если их меньше суммы позиций.
    mill = departments[0]
    order = _department_order(product, mill, status=status)
    Payment.objects.create(order=order, amount="4000.00", method="cash", status="confirmed")
    _department_order(product, mill, status=status)
    api = auth_client(cashier)

    assert order_overpaid(order) == Decimal("4000.00")
    assert auth_client(accountant).get(f"/api/orders/{order.id}/").data["overpaid_amount"] == "4000.00"
    assert _ids(api.get("/api/orders/to-refund/")) == [order.pk]
    summary = api.get("/api/orders/to-refund/", {"summary": "1"}).data
    assert summary == [{"currency": "KZT", "amount": "4000.00", "count": 1}]


# ── P6: «К отгрузке» — касса берёт предоплату ──────────────────────────────


def test_awaiting_shipment_lists_unpaid_waiting_orders(auth_client, cashier, boss, departments, product):
    mill, city = departments
    waiting = _department_order(product, mill)
    on_post = _department_order(product, mill, status="loading")
    partly = _department_order(product, mill, status="arrived")
    _prepay(partly, boss, "4000.00")
    paid = _department_order(product, mill)
    _prepay(paid, boss, "10000.00")
    _department_order(product, mill, status="pending")
    _department_order(product, mill, status="shipped")
    _department_order(product, city)
    api = auth_client(cashier)

    assert set(_ids(api.get("/api/orders/awaiting-shipment/"))) == {waiting.pk, on_post.pk, partly.pk}
    summary = api.get("/api/orders/awaiting-shipment/", {"summary": "1"}).data
    assert summary == [{"currency": "KZT", "amount": "26000.00", "count": 3}]


def test_cashier_lists_need_cashier_permission(auth_client, user_with_perms):
    viewer = user_with_perms("viewer", codes=["orders.view"])
    api = auth_client(viewer)

    assert api.get("/api/orders/awaiting-shipment/").status_code == 403
    assert api.get("/api/orders/to-refund/").status_code == 403


@pytest.mark.parametrize(
    "url",
    [
        "/api/orders/awaiting-shipment/",
        "/api/orders/awaiting-shipment/?summary=1",
        "/api/orders/to-refund/",
        "/api/orders/to-refund/?summary=1",
    ],
)
def test_cashier_lists_query_count_is_constant(accountant, boss, product, url, count_queries):
    def make():
        order = _order(product)
        _prepay(order, boss, "10000.00" if "refund" in url else "1000.00")
        if "refund" in url:
            services.correct_order_prices(order, boss, total_amount="5000.00")

    for _ in range(2):
        make()
    small = count_queries(accountant, url)
    for _ in range(5):
        make()
    assert count_queries(accountant, url) == small


# ── Отчёты: касса по дню денег, продажа по дню отгрузки ────────────────────


def _noon(day):
    return timezone.make_aware(datetime.combine(day, time(12)))


def test_prepayment_is_income_on_its_day_and_sale_on_shipment_day(auth_client, boss, product):
    pay_day, ship_day = date(2026, 9, 10), date(2026, 9, 11)
    order = _order(product)
    usd = _order(product, currency="USD")
    payments = [_prepay(order, boss, "4000.00").pk, _prepay(usd, boss, "100.00").pk]
    Payment.objects.filter(pk__in=payments).update(paid_at=_noon(pay_day), confirmed_at=_noon(pay_day))
    _ship(order, boss)
    Shipment.objects.filter(order=order).update(shipped_at=_noon(ship_day))
    api = auth_client(boss)

    def report(day):
        return api.get("/api/reports/summary/", {"date_from": day, "date_to": day}).json()

    paid = report(pay_day)
    assert paid["income"]["by_currency"] == {"KZT": "4000.00", "USD": "100.00"}
    assert paid["shipped"]["orders"] == 0

    shipped = report(ship_day)
    assert shipped["shipped"]["orders"] == 1
    assert shipped["shipped"]["revenue_by_currency"] == {"KZT": "10000.00"}
    assert shipped["shipped"]["paid_amount_by_currency"] == {"KZT": "4000.00"}
    assert shipped["shipped"]["debt_amount_by_currency"] == {"KZT": "6000.00"}
    # Деньги не посчитаны второй раз в день отгрузки.
    assert not shipped["income"]["by_currency"]
