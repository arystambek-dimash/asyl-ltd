import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.apipay import create_invoice
from apps.orders.models import (
    ApiPayInvoice, ApiPayWebhookEvent, Order, OrderItem, Payment,
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
    client = Client.objects.create(
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
        "external_order_id": f"order_{payment.order_id}",
        "external_order_id_idempotency": f"asyl-payment-{payment.id}",
    }
    assert invoice.invoice_id == 42
    assert invoice.status == "processing"


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


def test_webhook_does_not_confirm_mismatched_amount(api_client, settings):
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

    assert response.status_code == 400
    payment.refresh_from_db()
    assert payment.status == "received"
    assert not ApiPayWebhookEvent.objects.exists()
