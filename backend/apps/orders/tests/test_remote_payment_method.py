"""«Удалённая оплата»: отметка о деньгах, полученных вне кассы.

Способ ничего не выставляет клиенту — он сразу закрывает часть долга, как
наличные, но в отчётах остаётся безналичным поступлением.
"""

from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.reports import summary_report

pytestmark = pytest.mark.django_db


@pytest.fixture
def shipped_order():
    client = Client.objects.create_with_user(first_name="Нуржан", last_name="Сарыагаш", phone="+7 700 000 00 00")
    order = Order.objects.create(client=client, status="shipped", settlement_intent="debt")
    product = Product.objects.create(name="Мука", color="White", weight_kg="50")
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=Decimal("1000.00"))
    return order


def test_remote_payment_settles_at_once_without_an_invoice(payment_recorder, shipped_order, auth_client):
    response = auth_client(payment_recorder).post(
        f"/api/orders/{shipped_order.id}/payments/",
        {"amount": "400.00", "method": "remote"},
        format="json",
    )

    assert response.status_code == 201
    payment = Payment.objects.get(pk=response.data["id"])
    assert payment.method == "remote"
    assert payment.status == "confirmed"
    assert not hasattr(payment, "apipay_invoice")
    assert response.data["method_label"] == "Удалённая оплата"
    shipped_order.refresh_from_db()
    assert shipped_order.paid_total == Decimal("400.00")


def test_remote_payment_counts_as_cashless_income(payment_recorder, shipped_order, auth_client):
    auth_client(payment_recorder).post(
        f"/api/orders/{shipped_order.id}/payments/",
        {"amount": "1000.00", "method": "remote"},
        format="json",
    )

    income = summary_report(Order.objects.all(), income_only=True)["income"]

    assert income["cash"] == "0.00"
    assert income["cashless"] == "1000.00"
