import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.clients.services import client_analytics

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient(); c.force_authenticate(user); return c


def _order(client, status, qty=10, paid=None, intent="debt"):
    p = Product.objects.create(name=f"P{status}{qty}{paid}", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=client, status=status, settlement_intent=intent,
                             payment_status="unpaid")
    OrderItem.objects.create(order=o, product=p, quantity=qty, unit_price="100.00")  # 100*qty
    if paid:
        Payment.objects.create(order=o, amount=paid, status="confirmed")
    return o


def test_analytics_kpi():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    _order(c, "shipped", qty=10, paid="500")   # фин: revenue 1000, paid 500, долг 500
    _order(c, "pending", qty=5)                # не финансовый
    _order(c, "rejected", qty=3)               # отклонён
    a = client_analytics(c)
    assert a["kpi"]["revenue"] == "1000.00"
    assert a["kpi"]["paid"] == "500.00"
    assert a["kpi"]["debt"] == "500.00"
    assert a["kpi"]["orders_count"] == 1        # только финансовые
    assert a["kpi"]["rejected_count"] == 1
    assert a["kpi"]["average"] == "1000.00"


def test_analytics_by_status_and_recent():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    _order(c, "shipped", qty=10, paid="1000")
    _order(c, "rejected", qty=3)
    a = client_analytics(c)
    statuses = {row["status"]: row["count"] for row in a["by_status"]}
    assert statuses.get("shipped") == 1
    assert statuses.get("rejected") == 1
    assert len(a["recent_orders"]) == 2
    assert len(a["top_products"]) >= 1


def test_analytics_endpoint(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    _order(c, "shipped", qty=10, paid="0")
    r = _api(boss).get(f"/api/clients/{c.id}/analytics/")
    assert r.status_code == 200
    assert r.data["client"]["id"] == c.id
    assert "kpi" in r.data and "monthly" in r.data
