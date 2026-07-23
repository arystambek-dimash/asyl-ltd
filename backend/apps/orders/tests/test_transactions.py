from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.clients.models import Client
from apps.orders.models import (
    ApiPayInvoice,
    Order,
    OrderItem,
    Payment,
    PaymentRefund,
)


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
            order=kzt_order,
            amount="10.00",
            method="cash",
            status="confirmed",
            refunded_amount="3.00" if index == 0 else "0.00",
        )
        for index in range(12)
    ]
    ApiPayInvoice.objects.create(
        payment=payments[0],
        invoice_id=987,
        idempotency_key=f"asyl-payment-{payments[0].id}",
        status="paid",
        total_refunded="3.00",
    )
    PaymentRefund.objects.create(
        payment=payments[0],
        amount="3.00",
        method="cash",
        status="completed",
        reason="Частичный возврат",
        requested_by=accountant,
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
        "KZT": "117.00",
        "USD": "5.00",
    }
    assert response.data["summary"]["refunded_by_currency"] == {
        "KZT": "3.00",
        "USD": "0.00",
    }


def test_paid_qr_can_be_returned_from_cash_desk(
    auth_client, accountant,
):
    client = Client.objects.create(
        first_name="Возврат", phone="87770000000"
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        currency="KZT",
        payment_status="settled",
    )
    OrderItem.objects.create(order=order, quantity=1, unit_price="1.00")
    payment = Payment.objects.create(
        order=order,
        amount="1.00",
        method="kaspi",
        status="confirmed",
    )
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=990,
        channel="qr",
        idempotency_key=f"asyl-payment-{payment.id}",
        status="paid",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {
            "amount": "1.00",
            "reason": "Тестовый платёж",
            "mode": "auto",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["method"] == "cash"
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.refunded_amount == Decimal("1.00")
    assert payment.pending_refund_amount == Decimal("0.00")
    assert payment.available_for_refund == Decimal("0.00")
    assert order.paid_total == Decimal("0.00")
    assert order.payment_status == "unpaid"
    serialized = auth_client(accountant).get(
        "/api/payment-transactions/"
    ).data["results"][0]
    assert serialized["effective_status"] == "refunded"
    assert serialized["refunds"][0]["reason"] == "Тестовый платёж"


def test_manual_refund_requires_reason_and_cannot_exceed_available(
    auth_client, accountant,
):
    client = Client.objects.create(first_name="Лимит", phone="87770000006")
    order = Order.objects.create(client=client, status="shipped")
    payment = Payment.objects.create(
        order=order, amount="10.00", method="cash", status="confirmed"
    )

    missing_reason = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {"amount": "1.00"},
        format="json",
    )
    too_much = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {"amount": "11.00", "reason": "Ошибка"},
        format="json",
    )

    assert missing_reason.status_code == 400
    assert missing_reason.data["code"] == "refund_reason_required"
    assert too_much.status_code == 400
    assert too_much.data["code"] == "refund_exceeds_available"


@patch("apps.orders.apipay.api_request")
def test_phone_refund_is_reserved_until_provider_webhook(
    api_request, auth_client, accountant,
):
    api_request.return_value = {
        "refund": {
            "id": 501,
            "amount": "4.00",
            "status": "processing",
        }
    }
    client = Client.objects.create(first_name="Телефон", phone="87770000007")
    order = Order.objects.create(client=client, status="shipped")
    payment = Payment.objects.create(
        order=order, amount="10.00", method="kaspi", status="confirmed"
    )
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=995,
        channel="phone",
        idempotency_key=f"asyl-payment-{payment.id}",
        status="paid",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {"amount": "4.00", "reason": "Частичный возврат"},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["method"] == "apipay"
    payment.refresh_from_db()
    assert payment.refunded_amount == Decimal("0.00")
    assert payment.pending_refund_amount == Decimal("4.00")
    assert payment.available_for_refund == Decimal("6.00")
    assert payment.net_amount == Decimal("10.00")


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


def test_cashier_can_reject_pending_transaction_with_reason(
    auth_client, accountant,
):
    client = Client.objects.create(
        first_name="Клиент", phone="87770000003"
    )
    order = Order.objects.create(
        client=client, status="shipped", currency="KZT"
    )
    payment = Payment.objects.create(
        order=order, amount="100.00", method="cash", status="received"
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/reject/",
        {"reason": "Ошибочно внесённая оплата"},
        format="json",
    )

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == "rejected"
    assert "Ошибочно внесённая оплата" in payment.note


@patch("apps.orders.apipay.api_request")
def test_phone_kaspi_rejection_waits_for_provider_confirmation(
    api_request, auth_client, accountant,
):
    api_request.return_value = {
        "message": "Invoice cancellation queued",
        "invoice_id": 991,
    }
    client = Client.objects.create(
        first_name="Kaspi", phone="87770000004"
    )
    order = Order.objects.create(
        client=client, status="shipped", currency="KZT"
    )
    payment = Payment.objects.create(
        order=order, amount="100.00", method="kaspi", status="received"
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment, invoice_id=991, channel="phone",
        idempotency_key=f"asyl-payment-{payment.id}", status="pending",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/reject/",
        {"reason": "Клиент отказался"},
        format="json",
    )

    assert response.status_code == 202
    payment.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.status == "received"
    assert invoice.status == "cancelling"
    api_request.assert_called_once_with("POST", "/invoices/991/cancel", {})


def test_active_qr_transaction_cannot_be_rejected(auth_client, accountant):
    client = Client.objects.create(
        first_name="QR", phone="87770000005"
    )
    order = Order.objects.create(
        client=client, status="shipped", currency="KZT"
    )
    payment = Payment.objects.create(
        order=order, amount="100.00", method="kaspi", status="received"
    )
    ApiPayInvoice.objects.create(
        payment=payment, invoice_id=992, channel="qr",
        idempotency_key=f"asyl-payment-{payment.id}", status="pending",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/reject/",
        {"reason": "Клиент отказался"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "qr_cancel_unsupported"
