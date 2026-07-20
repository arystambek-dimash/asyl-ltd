from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _invoice_order(user):
    client = Client.objects.create(
        user=user, first_name="Иван", last_name="Петров", phone="1",
        company_name='ТОО "Сайрам нан"', iin="190440003203",
    )
    product = Product.objects.create(
        name="Мука пшеничная высшего сорта", color="Red",
        weight_kg=Decimal("50"), price=Decimal("316000.00"),
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


def test_client_downloads_generated_invoice(auth_client, client_user):
    order = _invoice_order(client_user)

    response = auth_client(client_user).get(f"/api/portal/orders/{order.id}/invoice/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "schet_na_oplatu" in response["Content-Disposition"]
    payload = b"".join(response.streaming_content)
    assert payload.startswith(b"%PDF")
    assert len(payload) > 10_000


def test_invoice_unavailable_before_selecting_method(auth_client, client_user):
    order = _invoice_order(client_user)
    order.payment_method = "pending"
    order.save(update_fields=["payment_method"])

    response = auth_client(client_user).get(f"/api/portal/orders/{order.id}/invoice/")

    assert response.status_code == 400
    assert response.data["code"] == "invoice_not_available"
