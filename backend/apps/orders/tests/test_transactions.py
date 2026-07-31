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


def test_transaction_capabilities_include_employee_permissions(
    auth_client,
    user_with_perms,
):
    client = Client.objects.create(first_name="Только", last_name="Просмотр")
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(order=order, quantity=2, unit_price="100.00")
    rejected = Payment.objects.create(
        order=order,
        amount="50.00",
        method="cash",
        status="rejected",
    )
    issuable = Payment.objects.create(
        order=order,
        amount="50.00",
        method="kaspi",
        status="received",
    )
    viewer = user_with_perms("payment-viewer", codes=["payments.view"])

    response = auth_client(viewer).get("/api/payment-transactions/")

    assert response.status_code == 200
    by_id = {row["id"]: row for row in response.data["results"]}
    assert by_id[rejected.id]["can_restore"] is False
    assert by_id[issuable.id]["can_issue"] is False


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


@patch("apps.orders.apipay.api_request")
def test_paid_qr_refund_is_reserved_in_apipay_until_provider_confirmation(
    api_request, auth_client, accountant,
):
    api_request.return_value = {
        "refund": {
            "id": 502,
            "amount": "1.00",
            "status": "pending",
            "reason": "Тестовый платёж",
        }
    }
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
    assert response.data["method"] == "apipay"
    assert response.data["status"] == "pending"
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.refunded_amount == Decimal("0.00")
    assert payment.pending_refund_amount == Decimal("1.00")
    assert payment.available_for_refund == Decimal("0.00")
    assert order.paid_total == Decimal("1.00")
    assert order.payment_status == "settled"
    serialized = auth_client(accountant).get(
        "/api/payment-transactions/"
    ).data["results"][0]
    assert serialized["effective_status"] == "refund_pending"
    assert serialized["refunds"][0]["reason"] == "Тестовый платёж"
    assert serialized["refunds"][0]["status"] == "pending"
    api_request.assert_called_once_with(
        "POST",
        "/invoices/990/refund",
        {"amount": 1.0, "reason": "Тестовый платёж"},
    )


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
    not_finite = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {"amount": "NaN", "reason": "Ошибка"},
        format="json",
    )
    fractional_tiyin = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {"amount": "1.001", "reason": "Ошибка"},
        format="json",
    )

    assert missing_reason.status_code == 400
    assert missing_reason.data["code"] == "refund_reason_required"
    assert too_much.status_code == 400
    assert too_much.data["code"] == "refund_exceeds_available"
    assert not_finite.status_code == 400
    assert fractional_tiyin.status_code == 400
    assert not PaymentRefund.objects.filter(payment=payment).exists()


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


def test_transaction_history_query_count_is_independent_of_row_count(
    auth_client, accountant,
):
    """Serializing more operations must not add a query per row.

    ``effective_status``/``can_issue`` read the provider invoice and
    ``can_restore`` compares the sibling payments against ``total_amount``,
    which sums the order items. All three relations have to stay eagerly
    loaded by the shared payment loading plan. Comparing two dataset sizes
    keeps this honest without pinning a brittle absolute query count.
    """
    import django.db
    from django.test.utils import CaptureQueriesContext

    client = Client.objects.create(
        first_name="Нагрузочный", last_name="Клиент", phone="87009990000"
    )

    def add_orders(count, offset):
        for index in range(count):
            order = Order.objects.create(
                client=client, status="shipped", currency="KZT"
            )
            OrderItem.objects.create(
                order=order, quantity=1, unit_price="100.00",
            )
            payment = Payment.objects.create(
                order=order, amount="100.00", method="kaspi",
                status="received",
            )
            ApiPayInvoice.objects.create(
                payment=payment, invoice_id=offset + index, channel="qr",
                idempotency_key=f"asyl-payment-{payment.id}",
                status="pending",
            )
            Payment.objects.create(
                order=order, amount="10.00", method="cash", status="rejected",
            )

    def count_queries():
        with CaptureQueriesContext(django.db.connection) as captured:
            response = auth_client(accountant).get(
                "/api/payment-transactions/"
            )
        assert response.status_code == 200
        return len(captured), len(response.data["results"])

    add_orders(2, 8000)
    small_queries, small_rows = count_queries()
    add_orders(10, 9000)
    large_queries, large_rows = count_queries()

    assert small_rows == 4 and large_rows == 24
    assert large_queries == small_queries


def test_summary_splits_paid_total_by_payment_method(auth_client, accountant):
    """A mixed payment must show what it consists of, not just the total.

    Taking 300k in cash and 400k by QR reads as "700k paid" everywhere, which
    is exactly what the cashier cannot act on. The split has to reconcile with
    the total, so a refund reduces its own method's share.
    """
    client = Client.objects.create(
        first_name="Смешанный", last_name="Клиент", phone="87005550000"
    )
    order = Order.objects.create(
        client=client, status="shipped", currency="KZT"
    )
    Payment.objects.create(
        order=order, amount="300000.00", method="cash", status="confirmed",
    )
    Payment.objects.create(
        order=order, amount="400000.00", method="kaspi", status="confirmed",
        refunded_amount="50000.00",
    )
    # Не подтверждённая оплата в разбивку не попадает.
    Payment.objects.create(
        order=order, amount="10000.00", method="invoice", status="received",
    )

    response = auth_client(accountant).get("/api/payment-transactions/")

    assert response.status_code == 200
    summary = response.data["summary"]
    assert summary["paid_by_method"]["KZT"] == {
        "cash": "300000.00",
        "kaspi": "350000.00",
    }
    # Разбивка обязана сходиться с итогом, иначе кассир увидит два разных числа.
    assert summary["paid_by_currency"]["KZT"] == "650000.00"


def test_transaction_status_counts_and_filter(auth_client, accountant):
    client = Client.objects.create(
        first_name="Статусный", last_name="Клиент", phone="87001112233")
    order = Order.objects.create(client=client, status="shipped")
    for status, n in (("confirmed", 2), ("rejected", 1), ("received", 3)):
        for _ in range(n):
            Payment.objects.create(
                order=order, amount="10.00", method="cash", status=status)

    data = auth_client(accountant).get("/api/payment-transactions/").data
    # Счётчики — по всем статусам, независимо от выбранного фильтра.
    assert data["status_counts"] == {
        "confirmed": 2, "rejected": 1, "received": 3}

    filtered = auth_client(accountant).get(
        "/api/payment-transactions/?status=rejected").data
    assert filtered["count"] == 1
    assert [row["status"] for row in filtered["results"]] == ["rejected"]
    # Пилюли не должны схлопываться после выбора фильтра.
    assert filtered["status_counts"]["confirmed"] == 2


def test_awaiting_customer_is_counted_and_filtered_as_requested(
    auth_client, accountant,
):
    client = Client.objects.create(
        first_name="Ожидающий", last_name="Клиент", phone="87007778899")
    order = Order.objects.create(client=client, status="shipped")
    provider_payment = Payment.objects.create(
        order=order, amount="100.00", method="invoice", status="received")
    ApiPayInvoice.objects.create(
        payment=provider_payment,
        idempotency_key=f"awaiting-customer-{provider_payment.id}",
        status="pending",
    )
    Payment.objects.create(
        order=order, amount="50.00", method="cash", status="received")

    data = auth_client(accountant).get(
        "/api/payment-transactions/").data
    assert data["status_counts"]["requested"] == 1
    assert data["status_counts"]["received"] == 1

    waiting = auth_client(accountant).get(
        "/api/payment-transactions/?status=requested").data
    assert waiting["count"] == 1
    assert waiting["results"][0]["effective_status"] == "awaiting_customer"
