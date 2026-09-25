"""Любые фактически полученные в CRM деньги закрываются сразу.

CRM endpoint уже требует ``payments.create`` и недоступен клиентам. Поэтому
наличные/QR, которые сотрудник отметил как полученные, не должны зависеть от
дополнительного права ``payments.confirm``. Только заявка из портала либо
ещё не оплаченный счёт остаются в очереди.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import ApiPayInvoice, Order, OrderItem, Payment
from apps.orders.services import create_client_payment

pytestmark = pytest.mark.django_db


def _order(total="100.00", status="shipped"):
    product = Product.objects.create(
        name=f"P-{Product.objects.count() + 1}", color="Red", weight_kg="50")
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


def test_staff_without_confirm_permission_is_confirmed_immediately(
    auth_client, payment_recorder,
):
    order = _order()

    response = _pay(auth_client, payment_recorder, order)

    assert response.status_code == 201, response.data
    assert response.data["status"] == "confirmed"
    payment = Payment.objects.get(order=order)
    assert payment.confirmed_by_id == payment_recorder.id
    order.refresh_from_db()
    assert order.paid_total == Decimal("100.00")


def test_qr_taken_at_the_till_is_confirmed_too(
    auth_client, payment_recorder,
):
    """QR в кассе — деньги уже на POS-терминале, ждать нечего."""
    order = _order()

    response = _pay(
        auth_client, payment_recorder, order, method="kaspi"
    )

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

    with patch("apps.orders.views.create_invoice"):
        response = _pay(auth_client, accountant, order, method="invoice")

    assert response.status_code == 201, response.data
    assert response.data["status"] == "requested"
    order.refresh_from_db()
    assert order.paid_total == Decimal("0"), "долг не гасится до оплаты"


def test_invoice_still_waits_in_the_cashier_queue(auth_client, accountant):
    """Счёт остаётся в очереди — касса закроет его по факту поступления."""
    order = _order()
    with patch("apps.orders.views.create_invoice"):
        created = _pay(auth_client, accountant, order, method="invoice")

    queue = auth_client(accountant).get("/api/orders/payments-queue/")

    assert created.data["id"] in [row["id"] for row in queue.data]


def test_every_providerless_portal_payment_waits_in_cashier_queue(
    auth_client, accountant,
):
    """Legacy portal methods must not disappear merely because they are card."""
    order = _order()
    payment = create_client_payment(order, "card", order.client.user)

    queue = auth_client(accountant).get("/api/orders/payments-queue/")

    assert payment.status == "received"
    assert payment.id in [row["id"] for row in queue.data]


def test_autoconfirm_never_creates_an_overpayment(auth_client, accountant):
    """Вторая оплата сверх суммы заказа отбивается, а не подтверждается."""
    order = _order(total="100.00")
    _pay(auth_client, accountant, order)

    second = _pay(auth_client, accountant, order)

    assert second.status_code == 400
    assert second.data["code"] == "payment_exceeds_remaining"
    order.refresh_from_db()
    assert order.paid_total == Decimal("100.00")
