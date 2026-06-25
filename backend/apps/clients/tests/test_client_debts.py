import pytest
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _product():
    return Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")


def _order(client, product, qty=2, payment_status="unpaid", paid=None, store=None):
    order = Order.objects.create(
        client=client,
        store=store,
        status="shipped",
        payment_status=payment_status,
    )
    OrderItem.objects.create(order=order, product=product, quantity=qty)
    if paid is not None:
        Payment.objects.create(order=order, amount=paid)
    return order


def test_client_debts_aggregate_by_client(boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    store = Store.objects.create(
        client=c, name="S", payment_schedule_type="monthly", payment_days=[25]
    )
    _order(c, p, qty=3, payment_status="unpaid", store=store)  # 300
    _order(c, p, qty=2, payment_status="partial", paid="50.00")  # 150 left
    _order(c, p, qty=1, payment_status="settled", paid="100.00")  # excluded

    r = _api(boss).get("/api/clients/debts/")

    assert r.status_code == 200
    row = next(x for x in r.data if x["client_id"] == c.id)
    assert row["debt_total"] == "450.00"
    assert row["orders_count"] == 2
    assert row["unpaid_count"] == 1
    assert row["partial_count"] == 1
    assert row["stores_count"] == 1


def test_client_debt_detail_returns_unsettled_orders(boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    unpaid = _order(c, p, qty=2, payment_status="unpaid")
    settled = _order(c, p, qty=1, payment_status="settled", paid="100.00")

    r = _api(boss).get(f"/api/clients/{c.id}/debt-detail/")

    assert r.status_code == 200
    assert r.data["debt_total"] == "200.00"
    ids = [row["id"] for row in r.data["orders"]]
    assert unpaid.id in ids
    assert settled.id not in ids
