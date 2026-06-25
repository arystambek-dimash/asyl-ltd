import pytest
from decimal import Decimal
from apps.clients.models import Client
from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem
from apps.orders import services


@pytest.fixture
def shipped_order(db):
    # Оплата доступна после отгрузки; логистический статус оплатой не меняется.
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=c, status="shipped")
    OrderItem.objects.create(order=o, product=p, quantity=1)
    return o


def test_reject_endpoint(db, manager, auth_client):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="pending")
    r = auth_client(manager).post(f"/api/orders/{o.id}/reject/")
    assert r.status_code == 200
    o.refresh_from_db(); assert o.status == "rejected"


def test_confirm_payment_endpoint(shipped_order, accountant, auth_client, make_user):
    pay = services.create_client_payment(shipped_order, "card", make_user(client=True))
    r = auth_client(accountant).post(
        f"/api/orders/{shipped_order.id}/payments/{pay.id}/confirm/")
    assert r.status_code == 200
    shipped_order.refresh_from_db()
    assert shipped_order.payment_status == "settled"


def test_approve_debt_endpoint(shipped_order, boss, auth_client):
    r = auth_client(boss).post(f"/api/orders/{shipped_order.id}/approve-debt/")
    assert r.status_code == 200
    shipped_order.refresh_from_db()
    assert shipped_order.debt_override is True
    assert shipped_order.settlement_intent == "debt"
