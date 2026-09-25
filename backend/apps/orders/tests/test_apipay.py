import io
import json
import urllib.error
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import IntegrityError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders import apipay, webhooks
from apps.orders.apipay import (
    ApiPayAPIError,
    ApiPayConfigurationError,
    ApiPayCredentials,
    api_request,
    create_invoice,
)
from apps.orders.models import (
    ApiPayInvoice,
    ApiPayRefund,
    ApiPayWebhookEvent,
    Order,
    OrderItem,
    Payment,
)
from apps.sales.models import Department
from apps.orders.tests.apipay_fakes import ProviderResponse, signed_webhook

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("apipay_department")]


def _payment():
    client = Client.objects.create_with_user(
        first_name="Клиент", phone="+7 700 123-45-67"
    )
    product = Product.objects.create(
        name="Товар", color="Red", weight_kg="50"
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


def _invoice(payment, invoice_id, *, status="pending", **fields) -> ApiPayInvoice:
    fields.setdefault("idempotency_key", f"asyl-payment-{payment.id}")
    return ApiPayInvoice.objects.create(payment=payment, invoice_id=invoice_id, status=status, **fields)


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_create_invoice_uses_api_key_and_required_payload(urlopen, settings):
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    urlopen.return_value = ProviderResponse({"id": 42, "status": "processing"})
    payment = _payment()

    invoice = create_invoice(payment, user=None)

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
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    urlopen.return_value = ProviderResponse({
        "id": 43,
        "status": "pending",
        "qr_token_url": "https://qr.kaspi.kz/example",
        "qr_image_url": "https://api.apipay.kz/qr/example.png",
        "qr_expires_at": "2026-07-23T09:05:00+00:00",
    })
    payment = _payment()

    invoice = create_invoice(payment, channel="qr", user=None)

    request = urlopen.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.apipay.kz/api/v1/invoices/qr"
    assert "phone_number" not in payload
    assert invoice.channel == "qr"
    assert invoice.qr_token_url == "https://qr.kaspi.kz/example"
    assert invoice.qr_image_url == "https://api.apipay.kz/qr/example.png"
    assert invoice.qr_expires_at.isoformat() == "2026-07-23T09:05:00+00:00"


def test_webhook_rejects_invalid_signature(api_client):
    response = api_client.post(
        "/api/webhooks/apipay/",
        data=b'{"event":"webhook.test"}',
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE="sha256=wrong",
    )
    assert response.status_code == 401
    assert not ApiPayWebhookEvent.objects.exists()


def test_webhook_test_is_accepted_and_idempotent(api_client):
    payload = {"event": "webhook.test", "timestamp": "2026-07-23T00:00:00Z"}

    first = signed_webhook(api_client, payload)
    second = signed_webhook(api_client, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert ApiPayWebhookEvent.objects.count() == 1


def test_unmapped_invoice_webhook_is_queued_and_replayed_after_mapping(api_client):
    payment = _payment()
    invoice = _invoice(payment, None, status="creating")
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

    response = signed_webhook(api_client, payload)

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
    invoice = apipay._map_and_apply_invoice(
        invoice.pk,
        payment.pk,
        9042,
        {"id": 9042, "status": "processing"},
        channel=invoice.channel,
        phone=invoice.phone_number,
    )

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
    api_client
):
    payment = _payment()
    invoice = _invoice(payment, 42, status="processing")
    first_payload = {
        "event": "invoice.status_changed",
        "invoice": {"id": 42, "amount": "5000.00", "status": "pending"},
        "timestamp": "2026-07-23T08:30:00Z",
    }
    next_transition_payload = {
        **first_payload,
        "timestamp": "2026-07-23T08:30:10Z",
    }

    first = signed_webhook(api_client, first_payload)
    next_transition = signed_webhook(
        api_client, next_transition_payload
    )
    exact_replay = signed_webhook(
        api_client, next_transition_payload
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


def test_status_changed_and_qr_scanned_pending_events_do_not_collide(api_client):
    payment = _payment()
    _invoice(payment, 45, status="processing")
    base_payload = {
        "invoice": {
            "id": 45,
            "amount": "5000.00",
            "status": "pending",
        },
        "timestamp": "2026-07-23T08:30:00Z",
    }

    status_changed = signed_webhook(api_client, {"event": "invoice.status_changed", **base_payload},
    )
    qr_scanned = signed_webhook(api_client, {"event": "invoice.qr_scanned", **base_payload},
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


def test_paid_webhook_confirms_payment_and_order(api_client):
    payment = _payment()
    invoice = _invoice(payment, 42)
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

    response = signed_webhook(api_client, payload)

    assert response.status_code == 200
    payment.refresh_from_db()
    payment.order.refresh_from_db()
    invoice.refresh_from_db()
    assert payment.status == "confirmed"
    assert payment.order.payment_status == "settled"
    assert invoice.status == "paid"
    assert invoice.paid_at.isoformat() == "2026-07-23T08:35:00+00:00"


def test_partially_refunded_webhook_confirms_gross_payment(api_client):
    payment = _payment()
    invoice = _invoice(payment, 43)
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

    response = signed_webhook(api_client, payload)

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


def test_cancelled_then_paid_is_supported(api_client):
    payment = _payment()
    invoice = _invoice(payment, 42)
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

    assert signed_webhook(api_client, cancelled).status_code == 200
    payment.refresh_from_db()
    assert payment.status == "rejected"
    assert signed_webhook(api_client, paid).status_code == 200
    payment.refresh_from_db()
    assert payment.status == "confirmed"
    assert invoice.webhook_events.count() == 2


def test_error_can_recover_to_pending_and_delayed_older_status_is_ignored(api_client):
    payment = _payment()
    invoice = _invoice(payment, 44, status="processing")
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

    assert signed_webhook(api_client, errored).json() == {"ok": True}
    assert signed_webhook(api_client, recovered).json() == {"ok": True}
    assert signed_webhook(api_client, delayed).json() == {"ok": True}

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


def test_late_superseded_qr_payment_releases_conflicting_replacement(api_client):
    payment = _payment()
    payment.status = "rejected"
    payment.save(update_fields=["status"])
    _invoice(payment, 46, status="superseded", channel="qr")
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

    response = signed_webhook(api_client, payload)

    assert response.status_code == 200
    payment.refresh_from_db()
    replacement.refresh_from_db()
    assert payment.status == "confirmed"
    assert replacement.status == "rejected"


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
            {"status": "paid", "amount": "1.00"},
            "invoice.amount does not match payment",
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
    invoice_fields,
    expected_error,
):
    payment = _payment()
    invoice = _invoice(payment, 47)
    payload = {
        "event": "invoice.status_changed",
        "invoice": {"id": 47, **invoice_fields},
        "timestamp": "2026-07-23T08:35:01Z",
    }

    response = signed_webhook(api_client, payload)

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
    assert not ApiPayRefund.objects.exists()


def test_webhook_apply_integrity_error_is_queued_and_exact_retry_applies(api_client):
    payment = _payment()
    _invoice(payment, 42)
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
        failed = signed_webhook(api_client, payload)

    assert failed.status_code == 200
    assert failed.json() == {"ok": True, "queued": True}
    event = ApiPayWebhookEvent.objects.get()
    assert event.processed_at is None
    assert event.attempt_count == 1
    assert event.processing_error == "simulated apply failure"
    payment.refresh_from_db()
    assert payment.status == "received"

    retried = signed_webhook(api_client, payload)

    assert retried.status_code == 200
    assert retried.json()["duplicate"] is True
    assert ApiPayWebhookEvent.objects.count() == 1
    event.refresh_from_db()
    assert event.processed_at is not None
    assert event.processing_error == ""
    payment.refresh_from_db()
    assert payment.status == "confirmed"


def test_webhook_non_duplicate_insert_integrity_error_is_not_acknowledged(api_client):
    payload = {"event": "webhook.test", "timestamp": "2026-07-23T00:00:00Z"}

    with patch(
        "apps.orders.webhooks.ApiPayWebhookEvent.objects.create",
        side_effect=IntegrityError("simulated non-unique insert failure"),
    ):
        response = signed_webhook(api_client, payload)

    assert response.status_code == 500
    assert response.json()["error"] == "webhook_processing_failed"
    assert not ApiPayWebhookEvent.objects.exists()


def test_refund_webhook_confirms_missed_gross_payment(api_client):
    payment = _payment()
    invoice = _invoice(payment, 48, channel="phone")
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

    response = signed_webhook(api_client, payload)

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


def test_refund_webhook_updates_transaction_totals(api_client):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    _invoice(payment, 42, status="paid", channel="phone")
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

    response = signed_webhook(api_client, payload)

    assert response.status_code == 200
    refund = ApiPayRefund.objects.get(refund_id=77)
    payment.refresh_from_db()
    assert refund.status == "completed"
    assert refund.amount == Decimal("1250.00")
    assert payment.refunded_amount == Decimal("1250.00")
    assert payment.pending_refund_amount == Decimal("0.00")
    assert payment.payment_refunds.get().status == "completed"


def test_refund_webhook_dedupes_by_refund_id_and_status(api_client):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    _invoice(payment, 42, status="paid", channel="phone")
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

    first = signed_webhook(api_client, first_payload)
    retry = signed_webhook(api_client, retry_payload)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert ApiPayWebhookEvent.objects.count() == 1
    assert ApiPayWebhookEvent.objects.get().semantic_key == "refund:77:completed"
    assert ApiPayRefund.objects.filter(refund_id=77).count() == 1


def test_refund_above_gross_is_retained_without_changing_totals(api_client):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    _invoice(payment, 49, status="paid", channel="phone")
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

    response = signed_webhook(api_client, payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "queued": True}
    event = ApiPayWebhookEvent.objects.get()
    assert event.processed_at is None
    assert event.processing_error == "refund.amount exceeds payment amount"
    payment.refresh_from_db()
    assert payment.refunded_amount == Decimal("0.00")
    assert payment.pending_refund_amount == Decimal("0.00")
    assert not ApiPayRefund.objects.exists()
    assert not payment.payment_refunds.exists()


def test_cumulative_refund_above_gross_is_retained_and_preserves_totals(api_client):
    payment = _payment()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    _invoice(payment, 50, status="paid", channel="phone")
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

    first = signed_webhook(api_client, first_payload)
    excessive = signed_webhook(api_client, excessive_payload)

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
    assert payment.refunded_amount == Decimal("3000.00")
    assert payment.pending_refund_amount == Decimal("0.00")
    assert ApiPayRefund.objects.filter(refund_id=80).exists()
    assert not ApiPayRefund.objects.filter(refund_id=81).exists()
    assert payment.payment_refunds.count() == 1


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_each_department_uses_its_own_key(urlopen, settings):
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    city = Department.objects.create(code="city", name="Нью-Сити")
    city.set_apipay_api_key("city-key")
    city.save()

    urlopen.return_value = ProviderResponse({"id": 42, "status": "processing"})
    create_invoice(_payment(), user=None)
    assert urlopen.call_args.args[0].headers["X-api-key"] == "server-only-key"

    urlopen.return_value = ProviderResponse({"id": 43, "status": "processing"})
    first_order = Order.objects.get(pk=ApiPayInvoice.objects.get().payment.order_id)
    city_order = Order.objects.create(
        client=first_order.client, status="shipped", currency="KZT", department="city"
    )
    OrderItem.objects.create(
        order=city_order, product=first_order.items.first().product,
        quantity=1, unit_price=Decimal("5000.00"),
    )
    city_payment = Payment.objects.create(
        order=city_order, amount="5000.00", method="kaspi", status="received"
    )
    create_invoice(city_payment, user=None)
    assert urlopen.call_args.args[0].headers["X-api-key"] == "city-key"


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_department_without_key_rejects_before_reserving(urlopen):
    Department.objects.create(code="nokey", name="Без ключа")
    payment = _payment()
    Order.all_objects.filter(pk=payment.order_id).update(department="nokey")

    with pytest.raises(ApiPayConfigurationError) as exc:
        create_invoice(payment, user=None)

    assert "Без ключа" in str(exc.value)
    assert not ApiPayInvoice.objects.exists()
    urlopen.assert_not_called()


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_order_without_department_row_is_not_configured(urlopen):
    payment = _payment()
    Order.all_objects.filter(pk=payment.order_id).update(department="ghost")
    with pytest.raises(ApiPayConfigurationError):
        create_invoice(payment, user=None)
    urlopen.assert_not_called()


def _pending_invoice(invoice_id: int = 42) -> ApiPayInvoice:
    payment = _payment()
    return _invoice(payment, invoice_id, channel="qr", idempotency_key=f"asyl-payment-{payment.pk}-v1")


def _paid_payload(invoice_id: int = 42) -> dict:
    return {
        "event": "invoice.status_changed",
        "timestamp": "2026-09-15T10:00:00+00:00",
        "invoice": {"id": invoice_id, "status": "paid", "amount": 5000},
    }


def test_webhook_maps_signature_to_department(api_client, apipay_department):
    invoice = _pending_invoice()

    response = signed_webhook(api_client, _paid_payload())

    assert response.status_code == 200, response.content
    event = ApiPayWebhookEvent.objects.get()
    assert event.department_id == apipay_department.pk
    assert event.invoice_id == invoice.pk
    invoice.refresh_from_db()
    assert invoice.status == "paid"


def test_webhook_signed_by_other_department_is_rejected(api_client):
    city = Department.objects.create(code="city", name="Нью-Сити")
    city.set_apipay_api_key("city-key")
    city.set_apipay_webhook_secret("city-hook")
    city.save()
    invoice = _pending_invoice()  # заказ в отделе main

    response = signed_webhook(api_client, _paid_payload(), secret="city-hook")

    assert response.status_code == 403
    assert response.json() == {"error": "invoice_department_mismatch"}
    assert not ApiPayWebhookEvent.objects.exists()
    invoice.refresh_from_db()
    assert invoice.status == "pending"


def test_webhook_for_unknown_invoice_keeps_department_and_refuses_foreign_mapping(
    api_client, apipay_department
):
    city = Department.objects.create(code="city", name="Нью-Сити")
    city.set_apipay_api_key("city-key")
    city.set_apipay_webhook_secret("city-hook")
    city.save()

    # Событие пришло раньше, чем счёт сохранился локально.
    response = signed_webhook(api_client, _paid_payload(77), secret="city-hook")
    assert response.status_code == 200
    event = ApiPayWebhookEvent.objects.get()
    assert event.department_id == city.pk
    assert event.processed_at is None

    # Счёт 77 оказался у отдела main: событие отдела city к нему не применяется.
    invoice = _pending_invoice(77)
    webhooks.replay_pending_apipay_webhooks(provider_invoice_id=77)
    event.refresh_from_db()
    invoice.refresh_from_db()
    assert event.processed_at is None
    assert event.processing_error == "invoice_department_mismatch"
    assert invoice.status == "pending"
    # Повтор не исправит чужой отдел: событие не считается сбоем сверки.
    stats = webhooks.replay_pending_apipay_webhooks(event_id=event.pk)
    assert (stats["failed"], stats["rejected"]) == (0, 1)


def test_webhook_without_any_secret_is_not_configured(api_client):
    Department.objects.update(apipay_webhook_secret_encrypted="")
    response = api_client.post(
        "/api/webhooks/apipay/",
        data=b'{"event":"webhook.test"}',
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE="sha256=deadbeef",
    )
    assert response.status_code == 503
    assert response.json() == {"error": "webhook_not_configured"}


CREDENTIALS = ApiPayCredentials(api_key="key")


@pytest.mark.parametrize("raw", [True, 12.7, 12.0, "12.0", "", None, 0, "-3", {"id": 1}])
def test_provider_id_rejects_non_integer_values(raw):
    # Один разбор id ApiPay для счетов, возвратов и вебхуков: int() молча
    # превратил бы True в 1, а 12.7 в 12.
    with pytest.raises(ValueError, match="refund.id is invalid"):
        apipay.positive_provider_id(raw, "refund.id")


@pytest.mark.parametrize("raw", [12, "12", " 12 "])
def test_provider_id_accepts_integers_and_digit_strings(raw):
    assert apipay.positive_provider_id(raw) == 12


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100.004", Decimal("100.00")),
        (5000, Decimal("5000.00")),
        ("-1", Decimal("-1.00")),
        ("NaN", None),
        ("Infinity", None),
        (None, None),
        ("abc", None),
    ],
)
def test_provider_money_parses_amount_to_cents(raw, expected):
    assert apipay.provider_money(raw) == expected


@pytest.mark.parametrize("raw", [0, "0.001", "-1", "NaN", None, "abc"])
def test_positive_provider_money_rejects_non_positive_amounts(raw):
    # Один разбор суммы возврата для вебхука и сверки возвратов.
    with pytest.raises(ValueError, match="refund.amount is invalid"):
        apipay.positive_provider_money(raw, "refund.amount")


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_api_request_list_error_body_is_provider_error(urlopen):
    urlopen.side_effect = urllib.error.HTTPError(
        "https://apipay.test/api/v1/x", 422, "Unprocessable", {}, io.BytesIO(b'["bad"]')
    )

    with pytest.raises(ApiPayAPIError) as exc:
        api_request("GET", "/x", credentials=CREDENTIALS)

    assert (exc.value.status_code, exc.value.error_code) == (422, "apipay_error")


@patch("apps.orders.apipay.urllib.request.urlopen")
def test_api_request_rejects_oversized_response(urlopen, monkeypatch):
    monkeypatch.setattr(apipay, "MAX_RESPONSE_BYTES", 16)
    upstream = ProviderResponse({"status": "paid", "padding": "x" * 64})
    urlopen.return_value = upstream

    with pytest.raises(ApiPayAPIError) as exc:
        api_request("GET", "/x", credentials=CREDENTIALS)

    assert (exc.value.status_code, exc.value.error_code) == (502, "invalid_apipay_response")


def test_webhook_failing_past_attempt_limit_stops_counting_as_failure(api_client):
    payment = _payment()
    _invoice(payment, 48)
    payload = {
        "event": "invoice.status_changed",
        "invoice": {"id": 48, "status": "paid"},
        "timestamp": "2026-07-23T08:35:01Z",
    }
    signed_webhook(api_client, payload)
    event = ApiPayWebhookEvent.objects.get()
    assert event.processed_at is None

    ApiPayWebhookEvent.objects.update(attempt_count=webhooks.WEBHOOK_MAX_RETRYABLE_ATTEMPTS - 1)
    stats = webhooks.replay_pending_apipay_webhooks(event_id=event.pk)

    assert (stats["failed"], stats["rejected"]) == (0, 1)
    event.refresh_from_db()
    # Событие остаётся в журнале и повторяется по таймеру — на случай, если его починят руками.
    assert event.processed_at is None
    assert event.next_attempt_at is not None
