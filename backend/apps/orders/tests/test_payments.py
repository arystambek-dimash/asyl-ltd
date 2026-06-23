import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _order(status="confirmed", price="100.00", qty=5):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price=price)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status)
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    return o


def test_partial_payment_keeps_status(auth_client, accountant):
    o = _order()  # total 500
    resp = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": "200.00"}, format="json"
    )
    assert resp.status_code == 201
    o.refresh_from_db()
    assert o.paid_total == Decimal("200.00")
    assert o.status == "confirmed"


def test_full_payment_sets_status_paid(auth_client, accountant):
    o = _order()  # total 500
    auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    o.refresh_from_db()
    assert o.is_fully_paid is True
    assert o.status == "paid"


def test_manager_cannot_record_payment(auth_client, manager):
    o = _order()
    resp = auth_client(manager).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    assert resp.status_code == 403
