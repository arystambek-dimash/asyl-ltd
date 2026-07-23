from decimal import Decimal

import pytest

from apps.clients.models import Client
from apps.orders.models import ApiPayInvoice, Order, Payment


pytestmark = pytest.mark.django_db


def test_transaction_history_is_paginated_with_complete_currency_totals(
    auth_client, accountant,
):
    client = Client.objects.create(
        first_name="Транзакционный", last_name="Клиент", phone="87001234567"
    )
    kzt_order = Order.objects.create(
        client=client, status="shipped", currency="KZT"
    )
    payments = [
        Payment.objects.create(
            order=kzt_order, amount="10.00", method="cash", status="confirmed"
        )
        for _ in range(12)
    ]
    ApiPayInvoice.objects.create(
        payment=payments[0],
        invoice_id=987,
        idempotency_key=f"asyl-payment-{payments[0].id}",
        status="paid",
        total_refunded="3.00",
    )
    usd_order = Order.objects.create(
        client=client, status="shipped", currency="USD"
    )
    Payment.objects.create(
        order=usd_order, amount="5.00", method="cash", status="confirmed"
    )

    response = auth_client(accountant).get(
        "/api/payment-transactions/?page=2&page_size=10"
    )

    assert response.status_code == 200
    assert response.data["count"] == 13
    assert response.data["page"] == 2
    assert response.data["pages"] == 2
    assert len(response.data["results"]) == 3
    assert response.data["summary"]["paid_by_currency"] == {
        "KZT": "120.00",
        "USD": "5.00",
    }
    assert response.data["summary"]["refunded_kzt"] == "3.00"


def test_transaction_search_runs_across_full_history(auth_client, accountant):
    matching = Client.objects.create(
        first_name="Айдана", last_name="Особенная", phone="87770000001"
    )
    other = Client.objects.create(
        first_name="Другой", last_name="Клиент", phone="87770000002"
    )
    for client in (matching, other):
        order = Order.objects.create(
            client=client, status="shipped", currency="KZT"
        )
        Payment.objects.create(
            order=order, amount="10.00", method="cash", status="confirmed"
        )

    response = auth_client(accountant).get(
        "/api/payment-transactions/?search=Особенная"
    )

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["client_name"] == matching.name
