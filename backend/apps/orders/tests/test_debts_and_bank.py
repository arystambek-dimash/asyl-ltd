import pytest
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


_counter = [0]


def _shipped_order(intent="debt"):
    _counter[0] += 1
    p = Product.objects.create(name=f"P{_counter[0]}", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="shipped", payment_status="unpaid",
                             settlement_intent=intent)
    OrderItem.objects.create(order=o, product=p, quantity=2)  # total 200
    return o


def test_debts_endpoint_lists_unsettled_shipped(boss):
    o = _shipped_order()
    settled = _shipped_order()
    settled.payment_status = "settled"; settled.save()
    r = _api(boss).get("/api/orders/debts/")
    assert r.status_code == 200
    ids = [row["id"] for row in r.data]
    assert o.id in ids
    assert settled.id not in ids


def test_pay_bank_settles_full(accountant):
    o = _shipped_order(intent="instant")
    r = _api(accountant).post(f"/api/orders/{o.id}/pay-bank/")
    assert r.status_code == 201
    o.refresh_from_db()
    assert o.payment_status == "settled"


def test_payments_history_in_order(accountant):
    o = _shipped_order()
    _api(accountant).post(f"/api/orders/{o.id}/payments/", {"amount": "50"}, format="json")
    r = _api(accountant).get(f"/api/orders/{o.id}/")
    assert r.status_code == 200
    assert len(r.data["payments"]) == 1
    assert r.data["payments"][0]["amount"] == "50.00"
    assert r.data["payments"][0]["method_label"] == "Наличные"


def test_check_overdue_endpoint(boss):
    from apps.clients.models import Store
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    Store.objects.create(client=c, name="S", payment_schedule_type="none")
    r = _api(boss).post("/api/stores/check-overdue/")
    assert r.status_code == 200
    assert "checked" in r.data
