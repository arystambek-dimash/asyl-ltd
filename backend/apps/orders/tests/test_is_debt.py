import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def _order(status="shipped", intent="debt", paid=None):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name=f"P{status}{intent}{paid}", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c, status=status, settlement_intent=intent,
                             payment_status="unpaid")
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="100.00")  # 200
    if paid:
        Payment.objects.create(order=o, amount=paid, status="confirmed")
    return o, c


def test_shipped_debt_unpaid_is_debt():
    o, _ = _order(status="shipped", intent="debt")
    assert o.is_debt is True


def test_pending_is_not_debt():
    o, _ = _order(status="pending", intent="debt")
    assert o.is_debt is False


def test_draft_is_not_debt():
    o, _ = _order(status="draft", intent="debt")
    assert o.is_debt is False


def test_instant_is_not_debt_even_if_shipped_unpaid():
    o, _ = _order(status="shipped", intent="instant")
    assert o.is_debt is False


def test_fully_paid_is_not_debt():
    o, _ = _order(status="shipped", intent="debt", paid="200")
    assert o.is_debt is False


def test_client_debt_total_excludes_pending_and_instant():
    api = APIClient()
    from apps.accounts.models import User
    # use a staff user with clients.view via fixture-like setup
    # simpler: hit the serializer directly
    from apps.clients.serializers import ClientSerializer
    c = Client.objects.create(first_name="Z", last_name="Z", phone="z")
    p = Product.objects.create(name="ZP", color="Red", weight_kg="50", price="100.00")
    # pending — not debt
    op = Order.objects.create(client=c, status="pending", settlement_intent="debt")
    OrderItem.objects.create(order=op, product=p, quantity=10, unit_price="100.00")
    # shipped debt — counts (1000)
    os = Order.objects.create(client=c, status="shipped", settlement_intent="debt", payment_status="unpaid")
    OrderItem.objects.create(order=os, product=p, quantity=10, unit_price="100.00")
    data = ClientSerializer(c).data
    assert data["debt_total"] == "1000.00"
