"""Денежные правила orders/debt.py: свободный остаток к оплате и статус оплаты."""
from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.debt import (
    available_to_pay,
    confirmed_and_reserved,
    order_payment_status,
    payment_status,
)
from apps.orders.models import Order, OrderItem, Payment


@pytest.mark.parametrize("total,paid,expected", [
    ("0", "0", "unpaid"),
    # Без цен платить не за что: деньги на заказе с нулевой суммой — не «оплачен».
    ("0", "50", "partial"),
    ("200", "0", "unpaid"),
    ("200", "150", "partial"),
    ("200", "200", "settled"),
    ("200", "250", "settled"),
])
def test_payment_status(total, paid, expected):
    assert payment_status(Decimal(total), Decimal(paid)) == expected


def _order(unit_price="100.00"):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    product = Product.objects.create(name="P", color="Red", weight_kg="50")
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(order=order, product=product, quantity=2, unit_price=unit_price)
    return order


@pytest.mark.django_db
def test_available_to_pay_counts_net_confirmed_and_open_reservations():
    order = _order()  # 200
    # Подтверждённая считается нетто: 100 − возврат 30 = 70.
    Payment.objects.create(
        order=order, amount="100", refunded_amount="30", method="cash", status="confirmed",
    )
    Payment.objects.create(order=order, amount="40", method="invoice", status="requested")
    Payment.objects.create(order=order, amount="20", method="kaspi", status="received")
    Payment.objects.create(order=order, amount="500", method="cash", status="rejected")

    assert confirmed_and_reserved(order.payments.all()) == (Decimal("70"), Decimal("60"))
    assert available_to_pay(order) == Decimal("70")
    assert order_payment_status(order) == "partial"


@pytest.mark.django_db
def test_available_to_pay_never_negative():
    order = _order()  # 200
    Payment.objects.create(order=order, amount="150", method="cash", status="confirmed")
    Payment.objects.create(order=order, amount="100", method="invoice", status="requested")

    assert available_to_pay(order) == Decimal("0")
    # Переданный список (под блокировкой) заменяет оплаты заказа.
    only_confirmed = list(order.payments.filter(status="confirmed"))
    assert available_to_pay(order, only_confirmed) == Decimal("50")


@pytest.mark.django_db
def test_zero_total_order_has_nothing_to_pay():
    order = _order(unit_price="0")

    assert available_to_pay(order) == Decimal("0")
    assert order_payment_status(order) == "unpaid"
