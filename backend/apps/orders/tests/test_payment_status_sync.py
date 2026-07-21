import pytest
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.services import sync_payment_status

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _fully_paid_but_stale():
    """Заказ полностью оплачен, но payment_status застрял на unpaid (легаси/дрейф)."""
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="shipped", payment_status="unpaid")
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="100.00")  # total 200
    Payment.objects.create(order=o, amount="200", method="cash", status="confirmed")
    return o


def test_sync_payment_status_settles_fully_paid():
    o = _fully_paid_but_stale()
    assert o.payment_status == "unpaid"  # дрейф
    sync_payment_status(o)
    o.refresh_from_db()
    assert o.payment_status == "settled"


def test_debts_excludes_fully_paid_even_if_status_stale(boss):
    o = _fully_paid_but_stale()  # paid_total == total, but status stale
    r = _api(boss).get("/api/clients/debts/")
    assert r.status_code == 200
    assert o.client_id not in [row["client_id"] for row in r.data]
