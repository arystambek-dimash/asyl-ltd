"""Счёт на оплату «нашим документом»: без провайдера, PDF из кассы.

Канал remote (по умолчанию) выставляет онлайн-счёт клиенту через провайдера;
канал document создаёт обычную часть оплаты, которую кассир подтверждает
вручную, и позволяет скачать PDF «Счёт на оплату».
"""
from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import ApiPayInvoice, Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _order(status="shipped", price="100.00", qty=5, **client_kwargs):
    prod = Product.objects.create(
        name="Премиум", color="Red", weight_kg="50", price=price)
    client = Client.objects.create(**{
        "first_name": "Гани", "last_name": "Таскен", "phone": "87762838451",
        "iin": "123456789012", "company_name": "ТОО Тест",
        **client_kwargs,
    })
    order = Order.objects.create(client=client, status=status)
    OrderItem.objects.create(
        order=order, product=prod, quantity=qty, unit_price=price)
    return order


def test_document_invoice_part_skips_provider(auth_client, accountant):
    order = _order()

    resp = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "invoice", "amount": "500.00", "channel": "document"},
        ]},
        format="json",
    )

    assert resp.status_code == 201
    payment = Payment.objects.get(order=order)
    assert payment.method == "invoice"
    # Провайдер не участвует: счёт живёт документом и подтверждается кассой.
    assert payment.status == "received"
    assert ApiPayInvoice.objects.count() == 0


def test_document_invoice_needs_no_phone(auth_client, accountant):
    order = _order()
    order.client.phone = ""
    order.client.save(update_fields=["phone"])

    resp = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "invoice", "amount": "100.00", "channel": "document"},
        ]},
        format="json",
    )

    assert resp.status_code == 201


def test_invoice_pdf_download(auth_client, accountant):
    order = _order()
    auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "invoice", "amount": "500.00", "channel": "document"},
        ]},
        format="json",
    )

    resp = auth_client(accountant).get(f"/api/orders/{order.id}/invoice-pdf/")

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert b"%PDF" in b"".join(resp.streaming_content)[:8]


def test_document_invoice_lands_in_cashier_queue_and_confirms(auth_client, accountant):
    order = _order()
    created = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "invoice", "amount": "500.00", "channel": "document"},
        ]},
        format="json",
    ).json()
    payment_id = created[0]["id"]

    queue = auth_client(accountant).get("/api/orders/payments-queue/").json()
    assert any(item["id"] == payment_id for item in queue)

    confirm = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment_id}/confirm/")
    assert confirm.status_code == 200
    payment = Payment.objects.get(pk=payment_id)
    assert payment.status == "confirmed"


def test_partial_invoice_pdf_builds_with_part_amount():
    from apps.orders.invoices import build_invoice_pdf

    order = _order()  # total 500
    pdf = build_invoice_pdf(order, amount=Decimal("120.00"))
    assert pdf[:4] == b"%PDF"


def test_single_payment_path_supports_document_channel(auth_client, accountant):
    order = _order()
    order.client.phone = ""
    order.client.save(update_fields=["phone"])

    resp = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"method": "invoice", "amount": "200.00", "channel": "document"},
        format="json",
    )

    assert resp.status_code == 201
    from apps.orders.models import ApiPayInvoice as Invoice
    assert Invoice.objects.count() == 0


def test_invoice_pdf_requires_invoice_payment(auth_client, accountant):
    order = _order()
    resp = auth_client(accountant).get(f"/api/orders/{order.id}/invoice-pdf/")
    assert resp.status_code == 400


def test_invoice_pdf_requires_client_requisites(auth_client, accountant):
    order = _order(iin="")
    auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "invoice", "amount": "500.00", "channel": "document"},
        ]},
        format="json",
    )
    resp = auth_client(accountant).get(f"/api/orders/{order.id}/invoice-pdf/")
    assert resp.status_code == 400
