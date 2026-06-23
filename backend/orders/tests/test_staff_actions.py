import pytest
from decimal import Decimal
from clients.models import Client
from catalog.models import Product
from orders.models import Order, OrderItem
from orders import services


@pytest.fixture
def confirmed_order(db):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=1)
    return o


def test_reject_endpoint(db, manager, auth_client):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="pending")
    r = auth_client(manager).post(f"/api/orders/{o.id}/reject/")
    assert r.status_code == 200
    o.refresh_from_db(); assert o.status == "rejected"


def test_confirm_payment_endpoint(confirmed_order, accountant, auth_client, make_user):
    pay = services.create_client_payment(confirmed_order, "card", make_user(client=True))
    r = auth_client(accountant).post(
        f"/api/orders/{confirmed_order.id}/payments/{pay.id}/confirm/")
    assert r.status_code == 200
    confirmed_order.refresh_from_db(); assert confirmed_order.status == "paid"


def test_approve_debt_endpoint(confirmed_order, boss, auth_client):
    r = auth_client(boss).post(f"/api/orders/{confirmed_order.id}/approve-debt/")
    assert r.status_code == 200
    confirmed_order.refresh_from_db()
    assert confirmed_order.status == "paid" and confirmed_order.debt_override is True
