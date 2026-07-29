import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from threading import Event, Lock
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.apipay import (
    ApiPayAPIError,
    apply_invoice_status,
    cancel_invoice,
    create_invoice,
)
from apps.orders.models import (
    ApiPayInvoice,
    ApiPayRefund,
    Order,
    OrderItem,
    Payment,
    PaymentRefund,
)
from apps.orders.services import create_client_payment

pytestmark = pytest.mark.django_db


class ProviderResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _order(*, total="100.00", client_user=None, currency="KZT"):
    client = Client.objects.create(
        user=client_user,
        first_name="Платёжный",
        last_name="Клиент",
        phone="87762838451",
    )
    product = Product.objects.create(
        name="Регрессионный товар",
        color="Red",
        weight_kg="50",
        price=total,
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        currency=currency,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price=Decimal(total),
    )
    return order


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_staff_mixed_payment_issues_phone_and_qr_invoices_server_side(
    urlopen, auth_client, accountant, settings,
):
    settings.APIPAY_API_KEY = "test-key"
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    urlopen.side_effect = [
        ProviderResponse({"id": 701, "status": "processing"}),
        ProviderResponse({
            "id": 702,
            "status": "pending",
            "qr_token_url": "https://qr.kaspi.kz/regression",
            "qr_image_url": "https://api.apipay.kz/qr/regression.png",
        }),
    ]
    order = _order()

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {
            "parts": [
                {"method": "cash", "amount": "20.00"},
                {
                    "method": "invoice",
                    "amount": "30.00",
                    "phone_number": "87762838451",
                },
                {"method": "kaspi", "amount": "50.00"},
            ],
            "note": "сервер выдаёт оба счёта",
        },
        format="json",
    )

    assert response.status_code == 201
    rows = {row["method"]: row for row in response.data}
    assert rows["cash"]["provider"] is None
    assert rows["invoice"]["provider"]["invoice_id"] == 701
    assert rows["invoice"]["provider"]["channel"] == "phone"
    assert rows["kaspi"]["provider"]["invoice_id"] == 702
    assert rows["kaspi"]["provider"]["channel"] == "qr"
    assert rows["kaspi"]["provider"]["qr_token_url"] == (
        "https://qr.kaspi.kz/regression"
    )

    assert urlopen.call_count == 2
    phone_request = urlopen.call_args_list[0].args[0]
    qr_request = urlopen.call_args_list[1].args[0]
    assert phone_request.full_url.endswith("/invoices")
    assert json.loads(phone_request.data)["phone_number"] == "87762838451"
    assert qr_request.full_url.endswith("/invoices/qr")
    assert "phone_number" not in json.loads(qr_request.data)


def test_mixed_provider_failure_rejects_parts_after_confirmed_compensation(
    auth_client, accountant,
):
    order = _order()

    def issue(payment, *, channel, phone_number=None):
        if channel == "phone":
            return ApiPayInvoice.objects.create(
                payment=payment,
                invoice_id=703,
                idempotency_key=f"asyl-payment-{payment.id}",
                channel="phone",
                phone_number=phone_number or "",
                status="pending",
            )
        raise ApiPayAPIError(503, "provider_unavailable", "Временно недоступно", {})

    def cancel(record):
        record.status = "cancelled"
        record.save(update_fields=["status", "updated_at"])
        return record

    with (
        patch("apps.orders.views.create_invoice", side_effect=issue),
        patch("apps.orders.views.cancel_invoice", side_effect=cancel) as cancel_mock,
    ):
        response = auth_client(accountant).post(
            f"/api/orders/{order.id}/payments/",
            {
                "parts": [
                    {"method": "cash", "amount": "20.00"},
                    {
                        "method": "invoice",
                        "amount": "30.00",
                        "phone_number": "87762838451",
                    },
                    {"method": "kaspi", "amount": "50.00"},
                ],
            },
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "provider_unavailable"
    assert set(order.payments.values_list("status", flat=True)) == {"rejected"}
    invoice = ApiPayInvoice.objects.get(invoice_id=703)
    assert invoice.status == "cancelled"
    cancel_mock.assert_called_once_with(invoice)


def test_mixed_provider_failure_keeps_uncertain_phone_invoice_reserved(
    auth_client, accountant,
):
    order = _order()

    def issue(payment, *, channel, phone_number=None):
        if channel == "phone":
            return ApiPayInvoice.objects.create(
                payment=payment,
                invoice_id=704,
                idempotency_key=f"asyl-payment-{payment.id}",
                channel="phone",
                phone_number=phone_number or "",
                status="pending",
            )
        raise ApiPayAPIError(503, "provider_unavailable", "Временно недоступно", {})

    with (
        patch("apps.orders.views.create_invoice", side_effect=issue),
        patch(
            "apps.orders.views.cancel_invoice",
            side_effect=ApiPayAPIError(
                503, "cancel_unknown", "Статус отмены неизвестен", {}
            ),
        ),
    ):
        response = auth_client(accountant).post(
            f"/api/orders/{order.id}/payments/",
            {
                "parts": [
                    {"method": "cash", "amount": "20.00"},
                    {
                        "method": "invoice",
                        "amount": "30.00",
                        "phone_number": "87762838451",
                    },
                    {"method": "kaspi", "amount": "50.00"},
                ],
            },
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "provider_unavailable"
    statuses = dict(order.payments.values_list("method", "status"))
    assert statuses == {
        "cash": "rejected",
        "invoice": "received",
        "kaspi": "rejected",
    }
    assert order.payments.get(method="invoice").apipay_invoice.status == "pending"


def test_rejected_payment_restore_cannot_overbook_remaining_balance(
    auth_client, accountant,
):
    order = _order()
    rejected = Payment.objects.create(
        order=order,
        amount="60.00",
        method="cash",
        status="rejected",
        recorded_by=accountant,
        received_by=accountant,
        received_at=timezone.now(),
    )
    Payment.objects.create(
        order=order,
        amount="50.00",
        method="cash",
        status="received",
        recorded_by=accountant,
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{rejected.id}/restore/"
    )

    assert response.status_code == 400
    assert response.data["code"] == "payment_exceeds_remaining"
    rejected.refresh_from_db()
    assert rejected.status == "rejected"


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_restore_legacy_rejected_invoice_issues_provider_invoice(
    urlopen, auth_client, accountant, settings,
):
    settings.APIPAY_API_KEY = "test-key"
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    urlopen.return_value = ProviderResponse({"id": 705, "status": "processing"})
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="40.00",
        method="invoice",
        status="rejected",
        recorded_by=accountant,
        received_by=accountant,
        received_at=timezone.now(),
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/restore/"
    )

    assert response.status_code == 200
    payment.refresh_from_db()
    assert payment.status == "received"
    invoice = payment.apipay_invoice
    assert invoice.invoice_id == 705
    assert invoice.channel == "phone"
    assert invoice.phone_number == "87762838451"
    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("/invoices")


def test_provider_payment_cannot_be_manually_confirmed_or_reopened(
    auth_client, accountant,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="kaspi",
        status="received",
        recorded_by=accountant,
        received_by=accountant,
        received_at=timezone.now(),
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=706,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="qr",
        status="pending",
    )

    confirm = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment.id}/confirm/"
    )
    assert confirm.status_code == 400
    assert confirm.data["code"] == "provider_payment_auto_confirmation"
    payment.refresh_from_db()
    assert payment.status == "received"

    payment.status = "confirmed"
    payment.confirmed_at = timezone.now()
    payment.save(update_fields=["status", "confirmed_at"])
    invoice.status = "paid"
    invoice.save(update_fields=["status", "updated_at"])
    reopen = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment.id}/reopen/"
    )
    assert reopen.status_code == 400
    assert reopen.data["code"] == "provider_payment_requires_refund"
    payment.refresh_from_db()
    assert payment.status == "confirmed"


def test_portal_does_not_release_or_reuse_staff_owned_pending_payment(
    auth_client, accountant, make_user,
):
    user = make_user(username="payment-owner", client=True)
    order = _order(client_user=user)
    staff_payment = Payment.objects.create(
        order=order,
        amount="40.00",
        method="cash",
        status="requested",
        recorded_by=accountant,
    )
    client = auth_client(user)

    release = client.post(
        f"/api/portal/orders/{order.id}/payments/{staff_payment.id}/release/"
    )
    assert release.status_code == 400
    assert release.data["code"] == "payment_not_found"

    create_own = client.post(
        f"/api/portal/orders/{order.id}/pay/",
        {"method": "cash"},
        format="json",
    )
    assert create_own.status_code == 201
    staff_payment.refresh_from_db()
    assert staff_payment.status == "requested"
    assert staff_payment.amount == Decimal("40.00")
    own_payment = order.payments.exclude(pk=staff_payment.pk).get()
    assert own_payment.recorded_by == user
    assert own_payment.amount == Decimal("60.00")


def test_portal_debt_choice_does_not_bulk_reject_pending_payments(
    auth_client, accountant, make_user,
):
    user = make_user(username="debt-owner", client=True)
    order = _order(client_user=user)
    pending = Payment.objects.create(
        order=order,
        amount="40.00",
        method="cash",
        status="requested",
        recorded_by=accountant,
    )

    response = auth_client(user).post(
        f"/api/portal/orders/{order.id}/pay/",
        {"method": "debt"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "payment_in_progress"
    pending.refresh_from_db()
    order.refresh_from_db()
    assert pending.status == "requested"
    assert order.debt_requested is False


@patch("apps.orders.apipay.api_request")
def test_explicit_apipay_refund_supports_paid_qr_without_cash_fallback(
    api_request, auth_client, accountant,
):
    api_request.return_value = {
        "refund": {
            "id": 808,
            "amount": "10.00",
            "status": "pending",
            "reason": "Проверка явного режима",
        }
    }
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="kaspi",
        status="confirmed",
        confirmed_by=accountant,
        confirmed_at=timezone.now(),
    )
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=707,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="qr",
        status="paid",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {
            "mode": "apipay",
            "amount": "10.00",
            "reason": "Проверка явного режима",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["method"] == "apipay"
    assert response.data["status"] == "pending"
    local_refund = PaymentRefund.objects.get(payment=payment)
    assert local_refund.method == "apipay"
    assert local_refund.status == "pending"
    payment.refresh_from_db()
    assert payment.refunded_amount == Decimal("0.00")
    assert payment.pending_refund_amount == Decimal("10.00")
    api_request.assert_called_once_with(
        "POST",
        "/invoices/707/refund",
        {"amount": 10.0, "reason": "Проверка явного режима"},
    )


def test_summary_report_uses_net_amount_after_refunds(
    auth_client, boss,
):
    order = _order(total="300.00")
    Payment.objects.create(
        order=order,
        amount="100.00",
        refunded_amount="40.00",
        method="cash",
        status="confirmed",
        confirmed_at=timezone.now(),
    )
    Payment.objects.create(
        order=order,
        amount="200.00",
        refunded_amount="25.00",
        method="invoice",
        status="confirmed",
        confirmed_at=timezone.now(),
    )

    response = auth_client(boss).get("/api/reports/summary/")

    assert response.status_code == 200
    income = response.data["income"]
    assert income["total"] == "235.00"
    assert income["cash"] == "60.00"
    assert income["cashless"] == "175.00"
    assert income["payments"] == 2
    # Возвраты вычтены и в раскладке по валютам.
    assert income["by_currency"] == {"KZT": "235.00"}
    assert response.data["days"][0]["received"] == "235.00"


@pytest.mark.parametrize(
    ("other_reservation", "expected_status"),
    [("0.00", "received"), ("70.00", "rejected")],
)
def test_error_to_pending_restores_reservation_only_when_capacity_allows(
    accountant, other_reservation, expected_status,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="40.00",
        method="invoice",
        status="rejected",
        recorded_by=accountant,
        received_by=accountant,
        received_at=timezone.now(),
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=708,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="phone",
        status="error",
    )
    if Decimal(other_reservation):
        Payment.objects.create(
            order=order,
            amount=other_reservation,
            method="cash",
            status="received",
            recorded_by=accountant,
        )

    changed = apply_invoice_status(
        invoice,
        {"id": 708, "status": "pending", "amount": "40.00"},
    )

    assert changed is True
    payment.refresh_from_db()
    invoice.refresh_from_db()
    assert invoice.status == "pending"
    assert payment.status == expected_status


def test_late_old_qr_payment_keeps_payable_replacement_visible_and_reserved(
    auth_client, accountant,
):
    order = _order()
    old_payment = Payment.objects.create(
        order=order,
        amount="60.00",
        method="kaspi",
        status="rejected",
        recorded_by=accountant,
        received_at=timezone.now(),
    )
    old_invoice = ApiPayInvoice.objects.create(
        payment=old_payment,
        invoice_id=709,
        idempotency_key=f"asyl-payment-{old_payment.id}",
        channel="qr",
        status="superseded",
        qr_token_url="https://qr.kaspi.kz/old",
    )
    replacement = Payment.objects.create(
        order=order,
        amount="60.00",
        method="invoice",
        status="received",
        recorded_by=accountant,
        received_at=timezone.now(),
    )
    ApiPayInvoice.objects.create(
        payment=replacement,
        invoice_id=710,
        idempotency_key=f"asyl-payment-{replacement.id}",
        channel="phone",
        status="pending",
        phone_number="87762838451",
    )

    changed = apply_invoice_status(
        old_invoice,
        {"id": 709, "status": "paid", "amount": "60.00"},
    )

    assert changed is True
    old_payment.refresh_from_db()
    replacement.refresh_from_db()
    assert old_payment.status == "confirmed"
    assert replacement.status == "received"
    assert (
        order.payments.filter(
            pk=replacement.pk,
            status__in=Payment.IN_PROGRESS_STATUSES,
            apipay_invoice__status="pending",
        ).exists()
    )

    history = auth_client(accountant).get("/api/payment-transactions/")
    assert history.status_code == 200
    replacement_row = next(
        row for row in history.data["results"] if row["id"] == replacement.id
    )
    assert replacement_row["effective_status"] == "awaiting_customer"
    assert replacement_row["provider"]["invoice_id"] == 710
    assert replacement_row["provider"]["status"] == "pending"


def test_late_qr_payment_keeps_received_cash_as_visible_conflict(
    auth_client, accountant,
):
    order = _order()
    old_payment = Payment.objects.create(
        order=order,
        amount="60.00",
        method="kaspi",
        status="rejected",
        recorded_by=accountant,
        received_at=timezone.now(),
    )
    old_invoice = ApiPayInvoice.objects.create(
        payment=old_payment,
        invoice_id=711,
        idempotency_key=f"asyl-payment-{old_payment.id}",
        channel="qr",
        status="superseded",
    )
    received_cash = Payment.objects.create(
        order=order,
        amount="60.00",
        method="cash",
        status="received",
        recorded_by=accountant,
        received_by=accountant,
        received_at=timezone.now(),
    )

    changed = apply_invoice_status(
        old_invoice,
        {"id": 711, "status": "paid", "amount": "60.00"},
    )

    assert changed is True
    old_payment.refresh_from_db()
    received_cash.refresh_from_db()
    assert old_payment.status == "confirmed"
    assert received_cash.status == "received"

    confirm = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{received_cash.id}/confirm/"
    )
    assert confirm.status_code == 400
    assert confirm.data["code"] == "payment_confirmation_overpayment"
    assert "40.00" in str(confirm.data["detail"])
    received_cash.refresh_from_db()
    assert received_cash.status == "received"


def test_legacy_no_amount_reuse_respects_every_other_reservation(
    accountant, make_user,
):
    user = make_user(username="legacy-payment-owner", client=True)
    order = _order(client_user=user)
    own_payment = Payment.objects.create(
        order=order,
        amount="20.00",
        method="cash",
        status="requested",
        recorded_by=user,
    )
    other_payment = Payment.objects.create(
        order=order,
        amount="40.00",
        method="cash",
        status="received",
        recorded_by=accountant,
        received_by=accountant,
        received_at=timezone.now(),
    )

    reused = create_client_payment(order, "kaspi", user, amount=None)

    assert reused.pk == own_payment.pk
    own_payment.refresh_from_db()
    other_payment.refresh_from_db()
    assert own_payment.amount == Decimal("60.00")
    assert own_payment.status == "received"
    assert other_payment.amount == Decimal("40.00")
    assert sum(
        order.payments.filter(
            status__in=Payment.IN_PROGRESS_STATUSES
        ).values_list("amount", flat=True),
        Decimal(0),
    ) == Decimal("100.00")


@pytest.mark.parametrize(
    ("current_status", "incoming_status"),
    [
        ("paid", "processing"),
        ("paid", "cancelling"),
        ("partially_refunded", "paid"),
        ("partially_refunded", "processing"),
        ("partially_refunded", "cancelling"),
    ],
)
def test_invoice_money_received_status_never_regresses(
    accountant, current_status, incoming_status,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="invoice",
        status="confirmed",
        recorded_by=accountant,
        confirmed_by=accountant,
        confirmed_at=timezone.now(),
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=712,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="phone",
        status=current_status,
        response_payload={"source": "money-received"},
    )

    changed = apply_invoice_status(
        invoice,
        {"id": 712, "status": incoming_status, "amount": "100.00"},
    )

    assert changed is False
    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert invoice.status == current_status
    assert invoice.response_payload == {"source": "money-received"}
    assert payment.status == "confirmed"


@patch("apps.orders.apipay.api_request")
def test_create_response_cannot_overwrite_paid_and_keeps_qr_fields(
    api_request_mock, accountant,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="kaspi",
        status="received",
        recorded_by=accountant,
        received_by=accountant,
        received_at=timezone.now(),
    )

    def paid_before_create_response(*_args):
        invoice = ApiPayInvoice.objects.get(payment=payment)
        ApiPayInvoice.objects.filter(pk=invoice.pk).update(invoice_id=713)
        invoice.refresh_from_db()
        assert apply_invoice_status(
            invoice,
            {"id": 713, "status": "paid", "amount": "100.00"},
        )
        return {
            "id": 713,
            "status": "processing",
            "qr_token_url": "https://qr.kaspi.kz/race",
            "qr_image_url": "https://api.apipay.kz/qr/race.png",
        }

    api_request_mock.side_effect = paid_before_create_response

    invoice = create_invoice(payment, channel="qr")

    payment.refresh_from_db()
    invoice.refresh_from_db()
    assert api_request_mock.call_count == 1
    assert payment.status == "confirmed"
    assert invoice.status == "paid"
    assert invoice.response_payload["status"] == "paid"
    assert invoice.qr_token_url == "https://qr.kaspi.kz/race"
    assert invoice.qr_image_url == "https://api.apipay.kz/qr/race.png"


@patch("apps.orders.apipay.api_request")
def test_qr_recovery_search_preserves_existing_fields_without_post(
    api_request_mock, accountant,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="kaspi",
        status="received",
        recorded_by=accountant,
    )
    record = ApiPayInvoice.objects.create(
        payment=payment,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="qr",
        status="creating",
        qr_token_url="https://qr.kaspi.kz/existing",
        qr_image_url="https://api.apipay.kz/qr/existing.png",
    )
    api_request_mock.return_value = {
        "current_page": 1,
        "total": 1,
        "data": [{
            "id": 714,
            "external_order_id": record.idempotency_key,
            "status": "processing",
        }],
    }

    invoice = create_invoice(payment, channel="qr")

    assert api_request_mock.call_count == 1
    assert api_request_mock.call_args.args[0] == "GET"
    assert "/invoices?search=" in api_request_mock.call_args.args[1]
    assert invoice.invoice_id == 714
    assert invoice.status == "processing"
    assert invoice.qr_token_url == "https://qr.kaspi.kz/existing"
    assert invoice.qr_image_url == "https://api.apipay.kz/qr/existing.png"


@pytest.mark.parametrize("provider_status", ["paid", "partially_refunded"])
@patch("apps.orders.apipay.api_request")
def test_create_money_response_confirms_payment_immediately(
    api_request_mock,
    accountant,
    provider_status,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="invoice",
        status="received",
        recorded_by=accountant,
    )
    api_request_mock.return_value = {
        "id": 720,
        "status": provider_status,
        "amount": "100.00",
    }

    invoice = create_invoice(payment, channel="phone")

    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert invoice.status == provider_status
    assert payment.status == "confirmed"
    assert payment.confirmed_at is not None


@patch("apps.orders.apipay.api_request")
def test_create_error_payload_maps_provider_invoice_before_raising(
    api_request_mock,
    accountant,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="invoice",
        status="received",
        recorded_by=accountant,
    )
    api_request_mock.side_effect = ApiPayAPIError(
        503,
        "provider_error",
        "provider rejected after creating invoice",
        {
            "invoice_id": 721,
            "status": "error",
            "amount": "100.00",
        },
    )

    with pytest.raises(ApiPayAPIError):
        create_invoice(payment, channel="phone")

    invoice = ApiPayInvoice.objects.get(payment=payment)
    payment.refresh_from_db()
    assert invoice.invoice_id == 721
    assert invoice.status == "error"
    assert payment.status == "rejected"


@patch("apps.orders.apipay.api_request")
def test_cancel_paid_response_confirms_instead_of_rejecting(
    api_request_mock,
    accountant,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="invoice",
        status="received",
        recorded_by=accountant,
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=722,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="phone",
        status="pending",
    )
    api_request_mock.return_value = {
        "invoice": {
            "id": 722,
            "status": "paid",
            "amount": "100.00",
        },
    }

    invoice = cancel_invoice(invoice)

    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert invoice.status == "paid"
    assert payment.status == "confirmed"


def test_paid_status_reconciles_soft_deleted_order(accountant):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="invoice",
        status="received",
        recorded_by=accountant,
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=723,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="phone",
        status="pending",
    )
    Order.all_objects.filter(pk=order.pk).update(deleted_at=timezone.now())

    assert apply_invoice_status(
        invoice,
        {"id": 723, "status": "paid", "amount": "100.00"},
    )

    payment.refresh_from_db()
    assert payment.status == "confirmed"
    assert Order.all_objects.get(pk=order.pk).payment_status == "settled"


@patch("apps.orders.apipay.api_request")
def test_refund_amount_mismatch_keeps_mapping_and_original_reservation(
    api_request_mock,
    auth_client,
    accountant,
):
    api_request_mock.return_value = {
        "refund": {
            "id": 824,
            "amount": "99.00",
            "status": "completed",
        }
    }
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="invoice",
        status="confirmed",
        confirmed_by=accountant,
        confirmed_at=timezone.now(),
    )
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=724,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="phone",
        status="paid",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/refund/",
        {
            "mode": "apipay",
            "amount": "10.00",
            "reason": "Проверка несовпадения",
        },
        format="json",
    )

    assert response.status_code == 201
    provider_refund = ApiPayRefund.objects.get(refund_id=824)
    local_refund = PaymentRefund.objects.get(payment=payment)
    payment.refresh_from_db()
    assert provider_refund.amount == Decimal("10.00")
    assert provider_refund.status == "pending"
    assert provider_refund.error_code == "provider_refund_amount_mismatch"
    assert provider_refund.response_payload["refund"]["amount"] == "99.00"
    assert local_refund.amount == Decimal("10.00")
    assert local_refund.status == "pending"
    assert payment.refunded_amount == Decimal("0.00")
    assert payment.pending_refund_amount == Decimal("10.00")


@pytest.mark.parametrize(
    ("payment_status", "invoice_status"),
    [("rejected", "expired"), ("confirmed", "paid")],
)
def test_legacy_kaspi_qr_endpoint_only_serves_active_payment(
    auth_client,
    accountant,
    payment_status,
    invoice_status,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="kaspi",
        status=payment_status,
        recorded_by=accountant,
    )
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=725,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="qr",
        status=invoice_status,
        qr_token_url="https://qr.kaspi.kz/stale",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{payment.id}/kaspi-qr/"
    )

    assert response.status_code == 404


@patch("apps.orders.apipay.api_request")
def test_cancel_response_cannot_downgrade_paid_webhook(
    api_request_mock, accountant,
):
    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="invoice",
        status="received",
        recorded_by=accountant,
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=715,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="phone",
        status="processing",
    )

    def paid_before_cancel_response(*_args):
        assert apply_invoice_status(
            invoice,
            {"id": 715, "status": "paid", "amount": "100.00"},
        )
        return {"status": "processing"}

    api_request_mock.side_effect = paid_before_cancel_response

    invoice = cancel_invoice(invoice)

    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert invoice.status == "paid"
    assert payment.status == "confirmed"


@pytest.mark.django_db(transaction=True)
def test_concurrent_create_invoice_calls_provider_once_and_preserves_qr(
    accountant,
):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory lock is production serialization")

    order = _order()
    payment = Payment.objects.create(
        order=order,
        amount="100.00",
        method="kaspi",
        status="received",
        recorded_by=accountant,
    )
    second_attempted = Event()
    mutex_count_lock = Lock()
    mutex_entries = 0

    from apps.orders import apipay

    real_mutex = apipay._invoice_issue_mutex

    @contextmanager
    def observed_mutex(payment_id):
        nonlocal mutex_entries
        with mutex_count_lock:
            mutex_entries += 1
            if mutex_entries == 2:
                second_attempted.set()
        with real_mutex(payment_id):
            yield

    def provider_response(*_args):
        assert second_attempted.wait(timeout=5)
        return {
            "id": 716,
            "status": "pending",
            "qr_token_url": "https://qr.kaspi.kz/concurrent",
            "qr_image_url": "https://api.apipay.kz/qr/concurrent.png",
        }

    def issue():
        close_old_connections()
        try:
            thread_payment = Payment.objects.get(pk=payment.pk)
            return create_invoice(thread_payment, channel="qr").pk
        finally:
            close_old_connections()

    with (
        patch("apps.orders.apipay._invoice_issue_mutex", observed_mutex),
        patch(
            "apps.orders.apipay.api_request",
            side_effect=provider_response,
        ) as api_request_mock,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(issue)
        second = pool.submit(issue)
        assert first.result(timeout=10) == second.result(timeout=10)

    invoice = ApiPayInvoice.objects.get(payment=payment)
    assert api_request_mock.call_count == 1
    assert invoice.invoice_id == 716
    assert invoice.qr_token_url == "https://qr.kaspi.kz/concurrent"
    assert invoice.qr_image_url == "https://api.apipay.kz/qr/concurrent.png"
