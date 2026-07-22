import pytest
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_store_debts_aggregates(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S1", payment_schedule_type="monthly", payment_days=[5])
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c, store=s, status="shipped", payment_status="unpaid")
    OrderItem.objects.create(order=o, product=p, quantity=3, unit_price="100.00")  # 300

    r = _api(boss).get("/api/stores/debts/")
    assert r.status_code == 200
    row = next(x for x in r.data if x["store_id"] == s.id)
    assert row["debt_total"] == "300.00"
    assert row["orders_count"] == 1
    assert row["store_name"] == "S1"


def test_store_debt_detail_returns_orders(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S1", payment_schedule_type="none")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c, store=s, status="shipped", payment_status="unpaid")
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="100.00")  # 200

    r = _api(boss).get(f"/api/stores/{s.id}/debt-detail/")
    assert r.status_code == 200
    assert r.data["debt_total"] == "200.00"
    assert r.data["window_open"] is True
    assert len(r.data["orders"]) == 1
    assert r.data["orders"][0]["id"] == o.id


def test_store_with_no_debt_excluded(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    Store.objects.create(client=c, name="Empty", payment_schedule_type="none")
    r = _api(boss).get("/api/stores/debts/")
    assert all(x["store_name"] != "Empty" for x in r.data)
