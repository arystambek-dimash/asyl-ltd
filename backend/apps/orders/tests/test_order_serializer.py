from decimal import Decimal
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.labels import order_payment_method_label, payment_method_label
from apps.orders.models import ApiPayInvoice, Order, OrderItem, Payment, PaymentRefund
from apps.orders.serializers import OrderSerializer, PaymentSerializer
from apps.orders.services import reopen_confirmed_payment, restore_rejected_payment

pytestmark = pytest.mark.django_db


def test_serializer_exposes_payment_fields():
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c)
    data = OrderSerializer(o).data
    assert data["payment_status"] == "unpaid"
    assert data["settlement_intent"] == "debt"
    assert data["payment_method"] == "debt"
    assert data["payment_method_label"] == "Долг"
    assert "remaining_amount" in data
    assert data["is_debt"] is False


@pytest.mark.parametrize(
    ("method", "label"),
    [("invoice", "Счёт на оплату"), ("kaspi", "QR"),
     ("cash", "Наличные"), ("debt", "Долг")],
)
def test_payment_method_labels(method, label):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone=method)
    order = Order.objects.create(client=client)
    payment = Payment.objects.create(order=order, amount="100", method=method)
    assert PaymentSerializer(payment).data["method_label"] == label


@pytest.mark.parametrize(
    ("status", "label"),
    [("requested", "Ожидает"), ("received", "В кассе"),
     ("confirmed", "Оплачено"), ("rejected", "Отклонено")],
)
def test_payment_status_labels(status, label):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone=status)
    order = Order.objects.create(client=client)
    payment = Payment.objects.create(order=order, amount=Decimal("100"), method="cash", status=status)
    assert PaymentSerializer(payment).data["status_label"] == label


def test_order_payment_method_label_covers_mixed_choice():
    # Бэк ставит Order.payment_method = "mixed" при оплате несколькими способами.
    assert order_payment_method_label("mixed") == "Смешанная"
    assert order_payment_method_label("kaspi") == payment_method_label("kaspi")


def test_fully_paid_order_does_not_offer_restoring_rejected_payment(user_with_perms):
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="87762838451"
    )
    product = Product.objects.create(
        name="Оплаченный товар",
        color="Red",
        weight_kg="50",
    )
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price="100",
    )
    Payment.objects.create(
        order=order,
        amount=Decimal("100"),
        method="kaspi",
        status="confirmed",
    )
    rejected = Payment.objects.create(
        order=order,
        amount=Decimal("100"),
        method="invoice",
        status="rejected",
    )

    assert PaymentSerializer(
        rejected, context=_as(user_with_perms("restore-full", codes=["payments.confirm"]))
    ).data["can_restore"] is False


def _as(user):
    return {"request": SimpleNamespace(user=user)}


def _shipped_order(status="shipped"):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="7001")
    order = Order.objects.create(client=client, status=status)
    OrderItem.objects.create(order=order, quantity=1, unit_price="100")
    return order


def test_can_restore_follows_the_restore_service(accountant):
    """Кнопка «Восстановить» не показывается там, где сервис откажет."""
    order = _shipped_order()
    free = Payment.objects.create(order=order, amount=Decimal("30"), method="cash", status="rejected")
    retired = Payment.objects.create(order=order, amount=Decimal("30"), method="kaspi", status="rejected")
    # Счёт так и не выдан провайдером, а ключ уже закрыт.
    ApiPayInvoice.objects.create(
        payment=retired, invoice_id=None, status="error",
        idempotency_key=f"asyl-payment-{retired.pk}",
    )

    assert PaymentSerializer(free, context=_as(accountant)).data["can_restore"] is True
    assert PaymentSerializer(retired, context=_as(accountant)).data["can_restore"] is False
    with pytest.raises(ValidationError) as error:
        restore_rejected_payment(retired, accountant)
    assert error.value.detail["code"] == "provider_issue_key_retired"


def test_can_restore_is_false_on_a_cancelled_order(accountant):
    order = _shipped_order(status="cancelled")
    rejected = Payment.objects.create(order=order, amount=Decimal("30"), method="cash", status="rejected")

    assert PaymentSerializer(rejected, context=_as(accountant)).data["can_restore"] is False
    with pytest.raises(ValidationError) as error:
        restore_rejected_payment(rejected, accountant)
    assert error.value.detail["code"] == "payment_not_open"


def test_can_reopen_is_false_while_a_refund_is_pending(accountant):
    order = _shipped_order()
    payment = Payment.objects.create(order=order, amount=Decimal("100"), method="cash", status="confirmed")
    assert PaymentSerializer(payment, context=_as(accountant)).data["can_reopen"] is True
    PaymentRefund.objects.create(
        payment=payment, amount=Decimal("10"), method="cash", status="pending", reason="Брак",
    )

    assert PaymentSerializer(payment, context=_as(accountant)).data["can_reopen"] is False
    with pytest.raises(ValidationError) as error:
        reopen_confirmed_payment(payment, accountant)
    assert error.value.detail["code"] == "payment_has_refunds"


def test_payment_actions_are_not_offered_without_a_request():
    """Без запроса права неизвестны — флаги не выдаются «как админу»."""
    order = _shipped_order()
    confirmed = Payment.objects.create(order=order, amount=Decimal("50"), method="cash", status="confirmed")
    rejected = Payment.objects.create(order=order, amount=Decimal("10"), method="cash", status="rejected")

    assert PaymentSerializer(confirmed).data["can_reopen"] is False
    assert PaymentSerializer(rejected).data["can_restore"] is False
