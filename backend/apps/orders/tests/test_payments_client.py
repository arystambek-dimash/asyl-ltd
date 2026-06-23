import pytest
from decimal import Decimal
from rest_framework.exceptions import ValidationError
from apps.clients.models import Client
from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem, Payment
from apps.orders import services


@pytest.fixture
def order(db):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=2)  # total 200
    return o


def test_client_payment_is_pending_and_does_not_pay(order, make_user):
    pay = services.create_client_payment(order, "kaspi", make_user(client=True))
    assert pay.status == "pending"
    assert pay.amount == Decimal("200")
    order.refresh_from_db()
    assert order.status == "confirmed"


def test_confirm_payment_marks_order_paid(order, make_user):
    pay = services.create_client_payment(order, "card", make_user(client=True))
    services.confirm_payment(pay, make_user(username="staff"))
    order.refresh_from_db()
    assert order.status == "paid"


def test_reject_payment_keeps_confirmed(order, make_user):
    pay = services.create_client_payment(order, "card", make_user(client=True))
    services.reject_payment(pay, make_user(username="staff"))
    pay.refresh_from_db(); order.refresh_from_db()
    assert pay.status == "rejected"
    assert order.status == "confirmed"


def test_approve_debt_marks_paid(order, make_user):
    services.approve_debt(order, make_user(username="boss"))
    order.refresh_from_db()
    assert order.status == "paid"
    assert order.debt_override is True


def test_client_payment_requires_confirmed(order, make_user):
    order.status = "pending"; order.save()
    with pytest.raises(ValidationError):
        services.create_client_payment(order, "card", make_user(client=True))
