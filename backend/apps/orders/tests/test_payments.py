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


def test_payment_note_saved_and_returned(auth_client, accountant):
    """Примечание бухгалтера сохраняется и отдаётся сериализатором."""
    o = _order(status="shipped")
    resp = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/",
        {"amount": "100.00", "method": "kaspi", "note": "перевод от директора"},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.data["note"] == "перевод от директора"
    assert resp.data["method"] == "kaspi"


@pytest.mark.parametrize("method", ["cash", "kaspi", "invoice"])
def test_cashier_methods_are_counted_only_after_manual_confirmation(
        auth_client, accountant, method):
    order = _order(status="shipped")

    created = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"amount": "100.00", "method": method}, format="json",
    )

    assert created.status_code == 201
    assert created.data["status"] == "received"
    order.refresh_from_db()
    assert order.paid_total == Decimal("0")

    confirmed = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{created.data['id']}/confirm/")
    assert confirmed.status_code == 200
    order.refresh_from_db()
    assert order.paid_total == Decimal("100.00")


def test_card_is_not_available_for_new_cashier_payment(auth_client, accountant):
    order = _order(status="shipped")
    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"amount": "100.00", "method": "card"}, format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == "bad_method"


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


@pytest.mark.parametrize("amount", ["not-a-number", "NaN", "-1", "10000000000"])
def test_invalid_payment_amount_returns_400(auth_client, accountant, amount):
    o = _order(status="shipped")

    response = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": amount}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "invalid_amount"
    assert not o.payments.exists()


def test_invalid_payment_method_returns_400(auth_client, accountant):
    o = _order(status="shipped")

    response = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/",
        {"amount": "10.00", "method": "wire"}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "bad_method"


def test_missing_nested_payment_returns_404(auth_client, accountant):
    o = _order(status="shipped")

    response = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/999999/confirm/")

    assert response.status_code == 404


def test_malformed_nested_payment_id_returns_404(auth_client, accountant):
    o = _order(status="shipped")

    response = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/not-a-number/confirm/")

    assert response.status_code == 404
