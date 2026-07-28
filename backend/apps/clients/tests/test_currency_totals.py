"""Денежные итоги не складывают разные валюты.

1000 ₸ и 5 $ — это не «1005». Итог всегда разложен по коду валюты заказа.
"""
import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.clients.serializers import ClientSerializer
from apps.clients.services import client_history
from apps.orders.debt import debt_by_currency, order_remaining
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _debt_order(client, currency, amount, paid=None):
    product = Product.objects.create(
        name=f"P-{currency}-{amount}", color="Red", weight_kg="50", price="1.00"
    )
    order = Order.objects.create(
        client=client, status="shipped", settlement_intent="debt",
        currency=currency,
    )
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price=amount
    )
    if paid:
        Payment.objects.create(order=order, amount=paid, status="confirmed")
    return order


@pytest.fixture
def client_with_two_currencies():
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    _debt_order(client, "KZT", "1000.00")
    _debt_order(client, "USD", "5.00")
    return client


def test_debt_by_currency_keeps_currencies_apart(client_with_two_currencies):
    totals = debt_by_currency(client_with_two_currencies.orders.all())
    assert {k: str(v) for k, v in totals.items()} == {
        "KZT": "1000.00", "USD": "5.00",
    }


def test_client_debt_total_is_per_currency(client_with_two_currencies):
    data = ClientSerializer(client_with_two_currencies).data
    assert data["debt_by_currency"] == {"KZT": "1000.00", "USD": "5.00"}
    # Однострочный debt_total остаётся, но описывает только основную валюту.
    assert data["debt_total"] == "1000.00"
    assert data["debt_currency"] == "KZT"


def test_client_history_summary_is_per_currency(client_with_two_currencies):
    summary = client_history(client_with_two_currencies)["summary"]
    assert summary["by_currency"]["KZT"]["debt"] == "1000.00"
    assert summary["by_currency"]["USD"]["debt"] == "5.00"
    assert summary["orders_count"] == 2


def test_remaining_never_negative_on_overpayment():
    """Переплата не уводит остаток в минус и не гасит долг других заказов."""
    client = Client.objects.create(first_name="C", last_name="D", phone="y")
    overpaid = _debt_order(client, "KZT", "100.00", paid="250.00")
    assert overpaid.remaining_amount < 0
    assert order_remaining(overpaid) == 0

    _debt_order(client, "KZT", "400.00")
    # 400 долга не должны превратиться в 250 из-за переплаты по другому заказу.
    assert str(debt_by_currency(client.orders.all())["KZT"]) == "400.00"
