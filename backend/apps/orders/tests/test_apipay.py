import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.apipay import create_invoice
from apps.orders.models import (
    ApiPayInvoice,
    ApiPayRefund,
    ApiPayWebhookEvent,
    Order,
    OrderItem,
    Payment,
)

pytestmark = pytest.mark.django_db


class UpstreamResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _payment():
    client = Client.objects.create_with_user(
        first_name="Клиент", phone="+7 700 123-45-67"
    )
    product = Product.objects.create(
        name="Товар", color="Red", weight_kg="50", price="5000"
    )
    order = Order.objects.create(
        client=client, status="shipped", currency="KZT"
    )
    OrderItem.objects.create(
        order=order, product=product, quantity=1,
        unit_price=Decimal("5000.00"),
    )
    return Payment.objects.create(
        order=order, amount="5000.00", method="kaspi", status="received"
    )


def _signed_post(api_client, settings, payload, secret="webhook-secret"):
    settings.APIPAY_WEBHOOK_SECRET = secret
    body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    signature = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return api_client.post(
        "/api/webhooks/apipay/",
        data=body,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=signature,
    )


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_create_invoice_uses_api_key_and_required_payload(urlopen, settings):
    settings.APIPAY_API_KEY = "server-only-key"
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    urlopen.return_value = UpstreamResponse({"id": 42, "status": "processing"})
    payment = _payment()

    invoice = create_invoice(payment)

    request = urlopen.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.apipay.kz/api/v1/invoices"
    assert request.headers["X-api-key"] == "server-only-key"
    assert payload == {
        "phone_number": "87001234567",
        "amount": 5000.0,
        "description": f"Заказ №{payment.order_id}",
        "external_order_id": f"asyl-payment-{payment.id}-v1",
        "external_order_id_idempotency": f"asyl-payment-{payment.id}-v1",
    }
    assert invoice.invoice_id == 42
    assert invoice.status == "processing"


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_create_qr_invoice_persists_payment_links(urlopen, settings):
    settings.APIPAY_API_KEY = "server-only-key"
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    urlopen.return_value = UpstreamResponse({
        "id": 43,
        "status": "pending",
        "qr_token_url": "https://qr.kaspi.kz/example",
        "qr_image_url": "https://api.apipay.kz/qr/example.png",
        "qr_expires_at": "2026-07-23T09:05:00+00:00",
    })
    payment = _payment()

    invoice = create_invoice(payment, channel="qr")

    request = urlopen.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.apipay.kz/api/v1/invoices/qr"
    assert "phone_number" not in payload
    assert invoice.channel == "qr"
    assert invoice.qr_token_url == "https://qr.kaspi.kz/example"
    assert invoice.qr_image_url == "https://api.apipay.kz/qr/example.png"
    assert invoice.qr_expires_at.isoformat() == "2026-07-23T09:05:00+00:00"


def test_webhook_rejects_invalid_signature(api_client, settings):
    settings.APIPAY_WEBHOOK_SECRET = "secret"
    response = api_client.post(
        "/api/webhooks/apipay/",
        data=b'{"event":"webhook.test"}',
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE="sha256=wrong",
    )
    assert response.status_code == 401
    assert not ApiPayWebhookEvent.objects.exists()


def test_webhook_test_is_accepted_and_idempotent(api_client, settings):
    payload = {"event": "webhook.test", "timestamp": "2026-07-23T00:00:00Z"}

    first = _signed_post(api_client, settings, payload)
    second = _signed_post(api_client, settings, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert ApiPayWebhookEvent.objects.count() == 1


def test_unmapped_invoice_webhook_is_queued_and_replayed_after_mapping(
    api_client, settings
):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=None,
        idempotency_key=f"asyl-payment-{payment.id}",
        status="creating",
    )
    payload = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 9042,
            "amount": "5000.00",
            "status": "paid",
            "paid_at": "2026-07-23T08:35:00Z",
        },
        "timestamp": "2026-07-23T08:35:01Z",
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "queued": True}
    event = ApiPayWebhookEvent.objects.get()
    assert event.provider_invoice_id == 9042
    assert event.invoice_id is None
    assert event.processed_at is None
    payment.refresh_from_db()
    assert payment.status == "received"

    # This is the second half of the real race: POST /invoices returned and
    # create_invoice has only now persisted ApiPay's invoice ID.
    invoice.invoice_id = 9042
    invoice.save(update_fields=["invoice_id", "updated_at"])

    event.refresh_from_db()
    invoice.refresh_from_db()
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    assert event.invoice_id == invoice.id
    assert event.processed_at is not None
    assert event.processing_error == ""
    assert invoice.status == "paid"
    assert payment.status == "confirmed"
    assert payment.order.payment_status == "settled"


def test_invoice_webhook_versions_changed_timestamp_and_dedupes_exact_replay(
    api_client, settings
):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=42,
        idempotency_key=f"asyl-payment-{payment.id}",
        status="processing",
    )
    first_payload = {
        "event": "invoice.status_changed",
        "invoice": {"id": 42, "amount": "5000.00", "status": "pending"},
        "timestamp": "2026-07-23T08:30:00Z",
    }
    next_transition_payload = {
        **first_payload,
        "timestamp": "2026-07-23T08:30:10Z",
    }

    first = _signed_post(api_client, settings, first_payload)
    next_transition = _signed_post(
        api_client, settings, next_transition_payload
    )
    exact_replay = _signed_post(
        api_client, settings, next_transition_payload
    )

    assert first.status_code == 200
    assert next_transition.status_code == 200
    assert "duplicate" not in next_transition.json()
    assert exact_replay.status_code == 200
    assert exact_replay.json()["duplicate"] is True
    assert ApiPayWebhookEvent.objects.count() == 2
    assert set(
        ApiPayWebhookEvent.objects.values_list("semantic_key", flat=True)
    ) == {
        (
            "invoice:42:invoice.status_changed:"
            "pending:2026-07-23T08:30:00Z"
        ),
        (
            "invoice:42:invoice.status_changed:"
            "pending:2026-07-23T08:30:10Z"
        ),
    }
    invoice.refresh_from_db()
    assert invoice.status == "pending"


def test_status_changed_and_qr_scanned_pending_events_do_not_collide(
    api_client, settings
):
    payment = _payment()
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=45,
        idempotency_key=f"asyl-payment-{payment.id}",
        status="processing",
    )
    base_payload = {
        "invoice": {
            "id": 45,
            "amount": "5000.00",
            "status": "pending",
        },
        "timestamp": "2026-07-23T08:30:00Z",
    }

    status_changed = _signed_post(
        api_client,
        settings,
        {"event": "invoice.status_changed", **base_payload},
    )
    qr_scanned = _signed_post(
        api_client,
        settings,
        {"event": "invoice.qr_scanned", **base_payload},
    )

    assert status_changed.json() == {"ok": True}
    assert qr_scanned.json() == {"ok": True}
    assert ApiPayWebhookEvent.objects.count() == 2
    semantic_keys = set(
        ApiPayWebhookEvent.objects.values_list("semantic_key", flat=True)
    )
    assert semantic_keys == {
        (
            "invoice:45:invoice.status_changed:"
            "pending:2026-07-23T08:30:00Z"
        ),
        (
            "invoice:45:invoice.qr_scanned:"
            "pending:2026-07-23T08:30:00Z"
        ),
    }


def test_paid_webhook_confirms_payment_and_order(api_client, settings):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment, invoice_id=42,
        idempotency_key=f"asyl-payment-{payment.id}", status="pending",
    )
    payload = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 42,
            "external_order_id": f"order_{payment.order_id}",
            "amount": "5000.00",
            "status": "paid",
            "paid_at": "2026-07-23T08:35:00Z",
        },
        "source": "Asyl LTD",
        "timestamp": "2026-07-23T08:35:01Z",
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.status == "confirmed"
    assert payment.order.payment_status == "settled"
    assert invoice.status == "paid"
    assert invoice.paid_at.isoformat() == "2026-07-23T08:35:00+00:00"


def test_partially_refunded_webhook_confirms_gross_payment(
    api_client, settings
):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=43,
        idempotency_key=f"asyl-payment-{payment.id}",
        status="pending",
    )
    payload = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 43,
            "amount": "5000.00",
            "currency": "KZT",
            "status": "partially_refunded",
            "paid_at": "2026-07-23T08:35:00Z",
        },
        "timestamp": "2026-07-23T08:36:00Z",
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.status == "confirmed"
    assert payment.amount == Decimal("5000.00")
    assert payment.refunded_amount == Decimal("0.00")
    assert payment.order.payment_status == "settled"
    assert invoice.status == "partially_refunded"


def test_cancelled_then_paid_is_supported(api_client, settings):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment, invoice_id=42,
        idempotency_key=f"asyl-payment-{payment.id}", status="pending",
    )
    cancelled = {
        "event": "invoice.status_changed",
        "invoice": {"id": 42, "amount": "5000.00", "status": "cancelled"},
        "timestamp": "2026-07-23T08:30:00Z",
    }
    paid = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 42, "amount": "5000.00", "status": "paid",
            "paid_at": "2026-07-23T08:35:00Z",
        },
        "timestamp": "2026-07-23T08:35:01Z",
    }

    assert _signed_post(api_client, settings, cancelled).status_code == 200
    payment.refresh_from_db()
    assert payment.status == "rejected"
    assert _signed_post(api_client, settings, paid).status_code == 200
    payment.refresh_from_db()
    assert payment.status == "confirmed"
    assert invoice.webhook_events.count() == 2


def test_error_can_recover_to_pending_and_delayed_older_status_is_ignored(
    api_client, settings
):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=44,
        idempotency_key=f"asyl-payment-{payment.id}",
        status="processing",
    )
    errored = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 44,
            "amount": "5000.00",
            "status": "error",
            "error_code": "temporary_provider_error",
        },
        "timestamp": "2026-07-23T08:30:00Z",
    }
    recovered = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 44,
            "amount": "5000.00",
            "status": "pending",
        },
        "timestamp": "2026-07-23T08:31:00Z",
    }
    delayed = {
        **errored,
        "timestamp": "2026-07-23T08:29:00Z",
    }

    assert _signed_post(api_client, settings, errored).json() == {"ok": True}
    assert _signed_post(api_client, settings, recovered).json() == {"ok": True}
    assert _signed_post(api_client, settings, delayed).json() == {"ok": True}

    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert invoice.status == "pending"
    assert invoice.provider_status_at.isoformat() == (
        "2026-07-23T08:31:00+00:00"
    )
    assert payment.status == "requested"
    assert ApiPayWebhookEvent.objects.count() == 3
    assert not ApiPayWebhookEvent.objects.filter(
        processed_at__isnull=True
    ).exists()


def test_late_superseded_qr_payment_releases_conflicting_replacement(
    api_client, settings
):
    payment = _payment()
    payment.status = "rejected"
    payment.save(update_fields=["status"])
    ApiPayInvoice.objects.create(
        payment=payment, invoice_id=46,
        idempotency_key=f"asyl-payment-{payment.id}", status="superseded",
        channel="qr",
    )
    replacement = Payment.objects.create(
        order=payment.order, amount=payment.amount,
        method="cash", status="requested",
    )
    payload = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 46, "amount": "5000.00", "status": "paid",
            "paid_at": "2026-07-23T08:35:00Z",
        },
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    payment.refresh_from_db()
    replacement.refresh_from_db()
    assert payment.status == "confirmed"
    assert replacement.status == "rejected"


def test_webhook_retains_mismatched_amount_for_durable_retry(
    api_client, settings
):
    payment = _payment()
    ApiPayInvoice.objects.create(
        payment=payment, invoice_id=42,
        idempotency_key=f"asyl-payment-{payment.id}", status="pending",
    )
    payload = {
        "event": "invoice.status_changed",
        "invoice": {"id": 42, "amount": "1.00", "status": "paid"},
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "queued": True}
    event = ApiPayWebhookEvent.objects.get()
    assert event.processed_at is None
    assert event.attempt_count == 1
    assert event.processing_error == "invoice.amount does not match payment"
    payment.refresh_from_db()
    assert payment.status == "received"


@pytest.mark.parametrize(
    ("invoice_fields", "expected_error"),
    [
        (
            {"status": "provider_future_state", "amount": "5000.00"},
            "invoice.status is invalid",
        ),
        (
            {"status": "paid"},
            "invoice.amount is required for money-received status",
        ),
        (
            {"status": "paid", "amount": "NaN"},
            "invoice.amount is invalid",
        ),
        (
            {
                "status": "paid",
                "amount": "5000.00",
                "currency": "USD",
            },
            "invoice.currency does not match order",
        ),
    ],
)
def test_invalid_money_webhooks_are_retained_without_mutation(
    api_client,
    settings,
    invoice_fields,
    expected_error,
):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=47,
        idempotency_key=f"asyl-payment-{payment.id}",
        status="pending",
    )
    payload = {
        "event": "invoice.status_changed",
        "invoice": {"id": 47, **invoice_fields},
        "timestamp": "2026-07-23T08:35:01Z",
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "queued": True}
    event = ApiPayWebhookEvent.objects.get()
    assert event.processed_at is None
    assert event.attempt_count == 1
    assert event.processing_error == expected_error
    payment.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.status == "received"
    assert payment.confirmed_at is None
    assert payment.refunded_amount == Decimal("0.00")
    assert invoice.status == "pending"
    assert invoice.provider_status_at is None
    assert invoice.total_refunded == Decimal("0.00")
    assert not ApiPayRefund.objects.exists()


def test_webhook_apply_integrity_error_is_queued_and_exact_retry_applies(
    api_client, settings
):
    payment = _payment()
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=42,
        idempotency_key=f"asyl-payment-{payment.id}",
        status="pending",
    )
    payload = {
        "event": "invoice.status_changed",
        "invoice": {
            "id": 42,
            "amount": "5000.00",
            "status": "paid",
            "paid_at": "2026-07-23T08:35:00Z",
        },
    }

    with patch(
        "apps.orders.webhooks.apply_invoice_status",
        side_effect=IntegrityError("simulated apply failure"),
    ):
        failed = _signed_post(api_client, settings, payload)

    assert failed.status_code == 200
    assert failed.json() == {"ok": True, "queued": True}
    event = ApiPayWebhookEvent.objects.get()
    assert event.processed_at is None
    assert event.attempt_count == 1
    assert event.processing_error == "simulated apply failure"
    payment.refresh_from_db()
    assert payment.status == "received"

    retried = _signed_post(api_client, settings, payload)

    assert retried.status_code == 200
    assert retried.json()["duplicate"] is True
    assert ApiPayWebhookEvent.objects.count() == 1
    event.refresh_from_db()
    assert event.processed_at is not None
    assert event.processing_error == ""
    payment.refresh_from_db()
    assert payment.status == "confirmed"


def test_webhook_non_duplicate_insert_integrity_error_is_not_acknowledged(
    api_client, settings
):
    payload = {"event": "webhook.test", "timestamp": "2026-07-23T00:00:00Z"}

    with patch(
        "apps.orders.webhooks.ApiPayWebhookEvent.objects.create",
        side_effect=IntegrityError("simulated non-unique insert failure"),
    ):
        response = _signed_post(api_client, settings, payload)

    assert response.status_code == 500
    assert response.json()["error"] == "webhook_processing_failed"
    assert not ApiPayWebhookEvent.objects.exists()


def test_refund_webhook_confirms_missed_gross_payment(api_client, settings):
    payment = _payment()
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=48,
        channel="phone",
        idempotency_key=f"asyl-payment-{payment.id}",
        status="pending",
    )
    payload = {
        "event": "invoice.refunded",
        "invoice": {
            "id": 48,
            "amount": "5000.00",
            "currency": "KZT",
            "status": "partially_refunded",
            "paid_at": "2026-07-23T08:35:00Z",
        },
        "refund": {
            "id": 78,
            "invoice_id": 48,
            "amount": "1250.00",
            "status": "completed",
            "reason": "Paid webhook был пропущен",
        },
        "timestamp": "2026-07-23T09:00:00Z",
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.status == "confirmed"
    assert payment.amount == Decimal("5000.00")
    assert payment.refunded_amount == Decimal("1250.00")
    assert payment.net_amount == Decimal("3750.00")
    assert payment.order.payment_status == "partial"
    assert invoice.status == "partially_refunded"
    assert invoice.total_refunded == Decimal("1250.00")


def test_refund_webhook_updates_transaction_totals(api_client, settings):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    invoice = ApiPayInvoice.objects.create(
        payment=payment, invoice_id=42, channel="phone",
        idempotency_key=f"asyl-payment-{payment.id}", status="paid",
    )
    payload = {
        "event": "invoice.refunded",
        "invoice": {"id": 42, "amount": "5000.00", "status": "paid"},
        "refund": {
            "id": 77,
            "invoice_id": 42,
            "amount": "1250.00",
            "status": "completed",
            "reason": "Возврат товара",
            "kaspi_refund_id": "K-77",
        },
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    refund = ApiPayRefund.objects.get(refund_id=77)
    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert refund.status == "completed"
    assert refund.amount == Decimal("1250.00")
    assert invoice.total_refunded == Decimal("1250.00")
    assert payment.refunded_amount == Decimal("1250.00")
    assert payment.pending_refund_amount == Decimal("0.00")
    assert payment.payment_refunds.get().status == "completed"


def test_refund_webhook_dedupes_by_refund_id_and_status(
    api_client, settings
):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=42,
        channel="phone",
        idempotency_key=f"asyl-payment-{payment.id}",
        status="paid",
    )
    first_payload = {
        "event": "invoice.refunded",
        "invoice": {"id": 42, "amount": "5000.00", "status": "paid"},
        "refund": {
            "id": 77,
            "invoice_id": 42,
            "amount": "1250.00",
            "status": "completed",
        },
        "timestamp": "2026-07-23T09:00:00Z",
    }
    retry_payload = {
        **first_payload,
        "timestamp": "2026-07-23T09:00:10Z",
    }

    first = _signed_post(api_client, settings, first_payload)
    retry = _signed_post(api_client, settings, retry_payload)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert ApiPayWebhookEvent.objects.count() == 1
    assert ApiPayWebhookEvent.objects.get().semantic_key == "refund:77:completed"
    assert ApiPayRefund.objects.filter(refund_id=77).count() == 1


def test_refund_above_gross_is_retained_without_changing_totals(
    api_client, settings
):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=49,
        channel="phone",
        idempotency_key=f"asyl-payment-{payment.id}",
        status="paid",
    )
    payload = {
        "event": "invoice.refunded",
        "invoice": {"id": 49, "amount": "5000.00", "status": "paid"},
        "refund": {
            "id": 79,
            "invoice_id": 49,
            "amount": "5000.01",
            "status": "completed",
        },
    }

    response = _signed_post(api_client, settings, payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "queued": True}
    event = ApiPayWebhookEvent.objects.get()
    assert event.processed_at is None
    assert event.processing_error == "refund.amount exceeds payment amount"
    payment.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.refunded_amount == Decimal("0.00")
    assert payment.pending_refund_amount == Decimal("0.00")
    assert invoice.total_refunded == Decimal("0.00")
    assert not ApiPayRefund.objects.exists()
    assert not payment.payment_refunds.exists()


def test_cumulative_refund_above_gross_is_retained_and_preserves_totals(
    api_client, settings
):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=50,
        channel="phone",
        idempotency_key=f"asyl-payment-{payment.id}",
        status="paid",
    )
    first_payload = {
        "event": "invoice.refunded",
        "invoice": {"id": 50, "amount": "5000.00", "status": "paid"},
        "refund": {
            "id": 80,
            "invoice_id": 50,
            "amount": "3000.00",
            "status": "completed",
        },
    }
    excessive_payload = {
        "event": "invoice.refunded",
        "invoice": {"id": 50, "amount": "5000.00", "status": "paid"},
        "refund": {
            "id": 81,
            "invoice_id": 50,
            "amount": "2500.00",
            "status": "completed",
        },
    }

    first = _signed_post(api_client, settings, first_payload)
    excessive = _signed_post(api_client, settings, excessive_payload)

    assert first.json() == {"ok": True}
    assert excessive.json() == {"ok": True, "queued": True}
    failed_event = ApiPayWebhookEvent.objects.get(
        semantic_key="refund:81:completed"
    )
    assert failed_event.processed_at is None
    assert failed_event.processing_error == (
        "completed refunds exceed payment amount"
    )
    payment.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.refunded_amount == Decimal("3000.00")
    assert payment.pending_refund_amount == Decimal("0.00")
    assert invoice.total_refunded == Decimal("3000.00")
    assert ApiPayRefund.objects.filter(refund_id=80).exists()
    assert not ApiPayRefund.objects.filter(refund_id=81).exists()
    assert payment.payment_refunds.count() == 1
