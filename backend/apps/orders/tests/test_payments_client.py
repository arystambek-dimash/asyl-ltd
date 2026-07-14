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
    o = Order.objects.create(client=c, status="shipped")
    OrderItem.objects.create(order=o, product=p, quantity=2)  # total 200
    return o


def test_client_payment_is_pending_and_does_not_pay(order, make_user):
    # Заявка клиента встаёт в цепочку на шаг «принята» — деньги ещё не учтены.
    pay = services.create_client_payment(order, "kaspi", make_user(client=True))
    assert pay.status == "received"
    assert pay.amount == Decimal("200")
    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.payment_status == "unpaid"


def test_confirm_payment_sets_settled(order, make_user):
    pay = services.create_client_payment(order, "card", make_user(client=True))
    services.accountant_confirm_payment(pay, make_user(username="acc"))
    order.refresh_from_db()
    # Логистический статус не меняется оплатой; меняется только статус оплаты.
    assert order.status == "shipped"
    assert order.payment_status == "settled"


def test_payment_chain_stamps_users(order, make_user):
    """Каждый переход цепочки фиксирует пользователя и время (ТЗ 4.5)."""
    manager = make_user(username="mgr")
    acc = make_user(username="acc")
    pay = services.add_payment(order, "200", manager, stage="requested")
    assert pay.status == "requested" and pay.recorded_by == manager
    services.receive_payment(pay, manager)
    assert pay.received_by == manager and pay.received_at is not None
    services.accountant_confirm_payment(pay, acc)
    pay.refresh_from_db()
    assert pay.status == "confirmed"
    assert pay.confirmed_by == acc and pay.confirmed_at is not None


def test_payment_chain_cannot_skip_received(order, make_user):
    """Нельзя подтвердить оплату, минуя стадию «принята»."""
    pay = services.add_payment(order, "200", make_user(username="mgr"), stage="requested")
    with pytest.raises(ValidationError):
        services.accountant_confirm_payment(pay, make_user(username="acc"))


def test_reject_payment_keeps_arrived(order, make_user):
    pay = services.create_client_payment(order, "card", make_user(client=True))
    services.reject_payment(pay, make_user(username="staff"))
    pay.refresh_from_db(); order.refresh_from_db()
    assert pay.status == "rejected"
    assert order.status == "shipped"


def test_approve_debt_sets_override(order, make_user):
    services.approve_debt(order, make_user(username="boss"))
    order.refresh_from_db()
    # Долг больше не меняет логистический статус — лишь фиксирует override.
    assert order.status == "shipped"
    assert order.debt_override is True
    assert order.settlement_intent == "debt"


def test_client_payment_requires_shipped(order, make_user):
    order.status = "arrived"; order.save()
    with pytest.raises(ValidationError):
        services.create_client_payment(order, "card", make_user(client=True))
