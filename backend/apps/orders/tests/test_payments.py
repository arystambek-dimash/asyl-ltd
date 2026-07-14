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


def _pay_through_chain(auth_client, accountant, order, amount):
    """Оплата через API: приём → подтверждение бухгалтером-кассой (деньги учтены)."""
    resp = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/", {"amount": amount}, format="json"
    )
    assert resp.status_code == 201
    pid = resp.data["id"]
    r = auth_client(accountant).post(f"/api/orders/{order.id}/payments/{pid}/confirm/")
    assert r.status_code == 200
    return pid


def test_partial_payment_keeps_logistics_status(auth_client, accountant):
    o = _order(status="shipped")  # total 500
    _pay_through_chain(auth_client, accountant, o, "200.00")
    o.refresh_from_db()
    assert o.paid_total == Decimal("200.00")
    assert o.status == "shipped"
    assert o.payment_status == "partial"


def test_full_payment_sets_settled(auth_client, accountant):
    o = _order(status="shipped")  # total 500
    _pay_through_chain(auth_client, accountant, o, "500.00")
    o.refresh_from_db()
    assert o.is_fully_paid is True
    assert o.payment_status == "settled"


def test_payment_not_counted_before_confirm(auth_client, accountant):
    """До подтверждения бухгалтером-кассой оплата не учтена."""
    o = _order(status="shipped")  # total 500
    resp = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    assert resp.status_code == 201
    o.refresh_from_db()
    assert o.paid_total == Decimal("0")
    assert o.payment_status == "unpaid"


def test_confirm_payment_requires_perm(auth_client, operator):
    """Без права payments.confirm подтверждение оплаты недоступно."""
    o = _order(status="shipped")
    from apps.orders.models import Payment
    p = Payment.objects.create(order=o, amount="100.00", status="received")
    r = auth_client(operator).post(f"/api/orders/{o.id}/payments/{p.id}/confirm/")
    assert r.status_code == 403


def test_payment_before_shipped_rejected(auth_client, accountant):
    o = _order(status="arrived")  # not yet shipped
    resp = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    assert resp.status_code == 400


def test_manager_cannot_record_payment(auth_client, manager):
    o = _order(status="shipped")
    resp = auth_client(manager).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    assert resp.status_code == 403
