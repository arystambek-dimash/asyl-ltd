"""Кассир не подтверждает оплату самому себе.

Очередь кассы нужна, чтобы деньги проверил второй человек. Когда оплату
вносит тот, кто её и подтверждает, очередь превращается в лишний клик без
проверки — такая оплата закрывается сразу. Менеджер без права подтверждения
по-прежнему отправляет деньги в кассу: контроль «двух рук» сохраняется.
"""

from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import ApiPayInvoice, Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def cashier_without_confirm(user_with_perms):
    """Менеджер: вносить оплату может, подтверждать — нет."""
    return user_with_perms(
        "no-confirm",
        codes=["orders.view", "payments.view", "payments.create"],
    )


def _order(total="100.00", status="shipped"):
    product = Product.objects.create(
        name=f"P-{Product.objects.count() + 1}", color="Red", weight_kg="50",
        price="1")
    client = Client.objects.create_with_user(
        first_name="Пла", last_name="Тельщик", phone="87001112233",
        iin="123456789012", company_name="ТОО Тест",
    )
    order = Order.objects.create(
        client=client, status=status, settlement_intent="debt")
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price=Decimal(total))
    return order


def _pay(auth_client, user, order, **body):
    return auth_client(user).post(
        f"/api/orders/{order.id}/payments/",
        {"amount": "100.00", "method": "cash", **body},
        format="json",
    )


def test_cashier_payment_is_confirmed_on_the_spot(auth_client, accountant):
    order = _order()

    response = _pay(auth_client, accountant, order)

    assert response.status_code == 201, response.data
    assert response.data["status"] == "confirmed"
    payment = Payment.objects.get(order=order)
    assert payment.status == "confirmed"
    assert payment.confirmed_by_id == accountant.id
    # Долг закрывается сразу, без прохода через очередь.
    order.refresh_from_db()
    assert order.paid_total == Decimal("100.00")


def test_cashier_payment_skips_the_confirmation_queue(auth_client, accountant):
    order = _order()
    _pay(auth_client, accountant, order)

    queue = auth_client(accountant).get("/api/orders/payments-queue/")

    assert queue.status_code == 200
    assert queue.data == [], "подтверждать самому себе нечего"


def test_manager_without_the_perm_still_queues_to_the_cashier(
    auth_client, cashier_without_confirm,
):
    """Контроль «двух рук» остаётся: чужую оплату проверяет касса."""
    order = _order()

    response = _pay(auth_client, cashier_without_confirm, order)

    assert response.status_code == 201, response.data
    assert response.data["status"] == "received"
    assert Payment.objects.get(order=order).confirmed_by_id is None


def test_qr_taken_at_the_till_is_confirmed_too(auth_client, accountant):
    """QR в кассе — деньги уже на POS-терминале, ждать нечего."""
    order = _order()

    response = _pay(auth_client, accountant, order, method="kaspi")

    assert response.status_code == 201, response.data
    assert response.data["status"] == "confirmed"
    assert not ApiPayInvoice.objects.exists()


def test_invoice_is_not_confirmed_before_the_client_pays(
    auth_client, accountant,
):
    """Счёт — выставленное обязательство, а не касса.

    Подтвердить его сразу значило бы погасить долг раньше, чем деньги
    поступили: заказ выглядел бы оплаченным по одному факту выставления.
    """
    order = _order()

    response = _pay(
        auth_client, accountant, order,
        method="invoice", channel="document",
    )

    assert response.status_code == 201, response.data
    assert response.data["status"] == "received"
    order.refresh_from_db()
    assert order.paid_total == Decimal("0"), "долг не гасится до оплаты"


def test_invoice_still_waits_in_the_cashier_queue(auth_client, accountant):
    """Счёт остаётся в очереди — касса закроет его по факту поступления."""
    order = _order()
    created = _pay(
        auth_client, accountant, order,
        method="invoice", channel="document",
    )

    queue = auth_client(accountant).get("/api/orders/payments-queue/")

    assert created.data["id"] in [row["id"] for row in queue.data]


def test_mixed_payment_by_a_cashier_confirms_every_part(
    auth_client, accountant,
):
    order = _order(total="150.00")

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "cash", "amount": "100.00"},
            {"method": "kaspi", "amount": "50.00"},
        ]},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert {row["status"] for row in response.data} == {"confirmed"}
    order.refresh_from_db()
    assert order.paid_total == Decimal("150.00")


def test_mixed_payment_confirms_cash_but_not_the_invoice_part(
    auth_client, accountant,
):
    """«100 наличными + 50 счётом»: в кассу легли только наличные."""
    order = _order(total="150.00")

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "cash", "amount": "100.00"},
            {"method": "invoice", "amount": "50.00", "channel": "document"},
        ]},
        format="json",
    )

    assert response.status_code == 201, response.data
    by_method = {row["method"]: row["status"] for row in response.data}
    assert by_method == {"cash": "confirmed", "invoice": "received"}
    order.refresh_from_db()
    assert order.paid_total == Decimal("100.00"), "счёт ещё не деньги"


def test_autoconfirm_never_creates_an_overpayment(auth_client, accountant):
    """Вторая оплата сверх суммы заказа отбивается, а не подтверждается."""
    order = _order(total="100.00")
    _pay(auth_client, accountant, order)

    second = _pay(auth_client, accountant, order)

    assert second.status_code == 400
    assert second.data["code"] == "payment_exceeds_remaining"
    order.refresh_from_db()
    assert order.paid_total == Decimal("100.00")
