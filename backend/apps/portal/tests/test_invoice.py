from decimal import Decimal
from unittest.mock import patch

import pytest
from reportlab.platypus import Paragraph as ReportLabParagraph

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.invoices import build_payment_receipt_pdf
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _invoice_order(user):
    client = Client.objects.create_with_user(
        user=user, first_name="Иван", last_name="Петров", phone="1",
        company_name='ТОО "Сайрам нан"', iin="190440003203",
    )
    product = Product.objects.create(
        name="Мука пшеничная высшего сорта", color="Red",
        weight_kg=Decimal("50"),
    )
    order = Order.objects.create(
        client=client, status="shipped", payment_method="invoice",
        settlement_intent="instant",
    )
    OrderItem.objects.create(
        order=order, product=product, quantity=35, unit_price=Decimal("316000.00"))
    order.payments.create(amount=order.total_amount, method="invoice", status="requested",
                          recorded_by=user)
    return order


def _render_receipt(payment):
    """PDF квитанции и текст всех её абзацев."""
    paragraphs = []

    def capture_paragraph(text, *args, **kwargs):
        paragraph = ReportLabParagraph(text, *args, **kwargs)
        paragraphs.append(paragraph)
        return paragraph

    with patch("apps.orders.invoices.Paragraph", side_effect=capture_paragraph):
        payload = build_payment_receipt_pdf(payment)
    return payload, "\n".join(paragraph.getPlainText() for paragraph in paragraphs)


def test_client_downloads_receipt_after_confirmed_payment(auth_client, client_user):
    order = _invoice_order(client_user)
    payment = order.payments.get()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])

    response = auth_client(client_user).get(
        f"/api/portal/orders/{order.id}/receipt/"
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "receipt_order" in response["Content-Disposition"]
    assert b"".join(response.streaming_content).startswith(b"%PDF")


def test_client_receipt_requires_confirmed_payment(auth_client, client_user):
    order = _invoice_order(client_user)

    response = auth_client(client_user).get(
        f"/api/portal/orders/{order.id}/receipt/"
    )

    assert response.status_code == 400
    assert response.data["code"] == "receipt_not_available"


def test_receipt_contains_asyl_ltd_statement_and_requisites(
    client_user, settings,
):
    order = _invoice_order(client_user)
    payment = order.payments.get()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])

    payload, rendered_text = _render_receipt(payment)

    assert payload.startswith(b"%PDF")
    assert f"Выписка {settings.INVOICE_SUPPLIER['short_name']}" in rendered_text
    assert settings.INVOICE_SUPPLIER["legal_name"] in rendered_text
    assert settings.INVOICE_SUPPLIER["bin"] in rendered_text
    assert settings.INVOICE_SUPPLIER["iban"] in rendered_text
    assert "ASUL LTD" not in rendered_text


def test_receipt_renders_dynamic_markup_as_text_without_loading_images(
    client_user, settings,
):
    order = _invoice_order(client_user)
    payment = order.payments.get()
    payment.status = "confirmed"
    payment.save(update_fields=["status"])
    injected = (
        'ТОО <Хлеб & Партнёры> "№1" '
        '<img src="http://127.0.0.1:65535/ssrf.png"/>'
    )
    settings.INVOICE_SUPPLIER = {
        **settings.INVOICE_SUPPLIER,
        "legal_name": injected,
        "bank": "Банк <Основной & партнёры>",
    }

    with patch(
        "reportlab.platypus.paraparser.ImageReader",
        side_effect=AssertionError("dynamic receipt text attempted to load an image"),
    ) as image_reader:
        payload, rendered_text = _render_receipt(payment)

    image_reader.assert_not_called()
    assert payload.startswith(b"%PDF")
    assert injected in rendered_text
    assert "Банк <Основной & партнёры>" in rendered_text
