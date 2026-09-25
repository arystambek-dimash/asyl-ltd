"""ApiPay без сети: ответ провайдера, подписанный вебхук и оплаченные заказы для тестов."""
import hashlib
import hmac
import json
from decimal import Decimal

from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import ApiPayInvoice, Order, OrderItem, Payment


class ProviderResponse:
    """Ответ ``urlopen`` с JSON-телом: подменяет HTTP-ответ ApiPay."""

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        body = json.dumps(self.payload).encode("utf-8")
        return body if size < 0 else body[:size]


def signed_webhook(api_client, payload, secret="webhook-secret"):
    """Вебхук, подписанный как ApiPay: секрет по умолчанию — у отдела ``main`` (``apipay_department``)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return api_client.post(
        "/api/webhooks/apipay/",
        data=body,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=signature,
    )


def shipped_order(*, total="100.00", client_user=None, currency="KZT"):
    """Отгруженный заказ на одну позицию суммой ``total``, без оплат."""
    client = Client.objects.create_with_user(
        user=client_user,
        first_name="Платёжный",
        last_name="Клиент",
        phone="87762838451",
    )
    product = Product.objects.create(
        name="Регрессионный товар",
        color="Red",
        weight_kg="50",
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


def paid_invoice(*, amount="100.00", invoice_id=800):
    """Оплаченный счёт ApiPay (канал phone) по отгруженному заказу на ``amount``."""
    client = Client.objects.create_with_user(
        first_name="Возврат",
        phone="87762838451",
    )
    product = Product.objects.create(
        name=f"Товар для возврата {invoice_id}",
        color="Red",
        weight_kg="50",
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        currency="KZT",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price=amount,
    )
    payment = Payment.objects.create(
        order=order,
        amount=amount,
        method="invoice",
        status="confirmed",
        confirmed_at=timezone.now(),
    )
    return ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=invoice_id,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel="phone",
        status="paid",
    )
