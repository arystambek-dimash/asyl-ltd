import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.services import create_client_payment
from apps.orders.serializers import OrderSerializer

pytestmark = pytest.mark.django_db


def _shipped_order(client):
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="50.00")
    o = Order.objects.create(client=client, status="shipped", payment_status="unpaid")
    OrderItem.objects.create(order=o, product=p, quantity=1, unit_price="50.00")  # total 50
    return o


def test_history_excludes_pending_payments(make_user):
    user = make_user(client=True)
    c = Client.objects.create(first_name="A", last_name="B", phone="x", user=user)
    o = _shipped_order(c)
    Payment.objects.create(order=o, amount="50", method="card", status="received")
    data = OrderSerializer(o).data
    # Неподтверждённая оплата не должна показываться как полученные деньги.
    assert data["payments"] == []
    assert data["paid_total"] == "0.00"


def test_pending_payments_visible_to_confirmer(make_user, accountant, auth_client):
    user = make_user(username="client-pay", client=True)
    c = Client.objects.create(first_name="A", last_name="B", phone="x", user=user)
    o = _shipped_order(c)
    Payment.objects.create(order=o, amount="50", method="card", status="received", recorded_by=user)

    response = auth_client(accountant).get(f"/api/orders/{o.id}/")

    assert response.status_code == 200
    assert response.data["payments"] == []
    assert response.data["pending_payments"][0]["amount"] == "50.00"


def test_history_shows_confirmed(make_user):
    user = make_user(client=True)
    c = Client.objects.create(first_name="A", last_name="B", phone="x", user=user)
    o = _shipped_order(c)
    Payment.objects.create(order=o, amount="50", method="cash", status="confirmed")
    data = OrderSerializer(o).data
    assert len(data["payments"]) == 1


def test_client_pay_does_not_duplicate_pending(make_user):
    user = make_user(client=True)
    c = Client.objects.create(first_name="A", last_name="B", phone="x", user=user)
    o = _shipped_order(c)
    create_client_payment(o, "card", user)
    create_client_payment(o, "kaspi", user)
    create_client_payment(o, "card", user)
    # Несколько кликов «оплатил» не плодят дубли — одна заявка на оплату.
    assert o.payments.filter(status="received").count() == 1
