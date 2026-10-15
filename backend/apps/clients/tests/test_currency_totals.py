"""Денежные итоги не складывают разные валюты.

1000 ₸ и 5 $ — это не «1005». Итог всегда разложен по коду валюты заказа.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.clients.serializers import ClientReadSerializer
from apps.clients.services import client_history
from apps.orders.debt import debt_by_currency, order_remaining
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _debt_order(client, currency, amount, paid=None):
    product = Product.objects.create(
        name=f"P-{currency}-{amount}", color="Red", weight_kg="50"
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
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    _debt_order(client, "KZT", "1000.00")
    _debt_order(client, "USD", "5.00")
    return client


def test_debt_by_currency_keeps_currencies_apart(client_with_two_currencies):
    totals = debt_by_currency(client_with_two_currencies.orders.all())
    assert {k: str(v) for k, v in totals.items()} == {
        "KZT": "1000.00", "USD": "5.00",
    }


def test_client_debt_total_is_per_currency(client_with_two_currencies):
    data = ClientReadSerializer(client_with_two_currencies).data
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
    client = Client.objects.create_with_user(first_name="C", last_name="D", phone="y")
    overpaid = _debt_order(client, "KZT", "100.00", paid="250.00")
    assert overpaid.remaining_amount < 0
    assert order_remaining(overpaid) == 0

    _debt_order(client, "KZT", "400.00")
    # 400 долга не должны превратиться в 250 из-за переплаты по другому заказу.
    assert str(debt_by_currency(client.orders.all())["KZT"]) == "400.00"


def test_client_overpaid_matches_history_summary():
    """«Мы должны клиенту» в списке — та же переплата, что в карточке клиента."""
    client = Client.objects.create_with_user(first_name="E", last_name="F", phone="z")
    _debt_order(client, "KZT", "100.00", paid="250.00")
    _debt_order(client, "USD", "5.00")

    data = ClientReadSerializer(client).data
    assert data["overpaid_by_currency"] == {"KZT": "150.00"}
    assert data["debt_by_currency"] == {"USD": "5.00"}
    assert client_history(client)["summary"]["by_currency"]["KZT"]["overpaid"] == "150.00"


def test_client_list_reads_overpaid_in_bulk(auth_client, user_with_perms):
    viewer = user_with_perms("overpaid-viewer", codes=["clients.view", "reports.view"])
    api = auth_client(viewer)

    def fetch():
        with CaptureQueriesContext(connection) as queries:
            resp = api.get("/api/clients/")
        assert resp.status_code == 200
        return {row["id"]: row for row in resp.data}, len(queries)

    first = Client.objects.create_with_user(first_name="G", last_name="H", phone="1")
    _debt_order(first, "KZT", "100.00", paid="250.00")
    _, one_client = fetch()
    for n in range(3):
        other = Client.objects.create_with_user(first_name=f"I{n}", last_name="J", phone=f"2{n}")
        _debt_order(other, "KZT", f"10{n + 1}.00", paid="130.00")
    rows, four_clients = fetch()

    assert four_clients == one_client
    assert rows[first.pk]["overpaid_by_currency"] == {"KZT": "150.00"}
    assert rows[first.pk]["debt_by_currency"] == {}
