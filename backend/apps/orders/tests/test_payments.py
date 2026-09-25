import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _order(status="confirmed", price="100.00", qty=5):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50")
    c = Client.objects.create_with_user(
        first_name="L", last_name="К", phone="87762838451"
    )
    o = Order.objects.create(client=c, status=status)
    OrderItem.objects.create(order=o, product=prod, quantity=qty, unit_price=price)
    return o


def _pay(auth_client, accountant, order, amount):
    """Касса вносит оплату — она сразу учтена (confirmed)."""
    resp = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/", {"amount": amount}, format="json"
    )
    assert resp.status_code == 201
    assert resp.data["status"] == "confirmed"
    return resp.data["id"]


def test_partial_payment_keeps_logistics_status(auth_client, accountant):
    o = _order(status="shipped")  # total 500
    _pay(auth_client, accountant, o, "200.00")
    o.refresh_from_db()
    assert o.paid_total == Decimal("200.00")
    assert o.status == "shipped"
    assert o.payment_status == "partial"


def test_full_payment_sets_settled(auth_client, accountant):
    o = _order(status="shipped")  # total 500
    _pay(auth_client, accountant, o, "500.00")
    o.refresh_from_db()
    assert o.is_fully_paid is True
    assert o.payment_status == "settled"


def test_confirmed_payment_can_be_reopened_with_audit_log(auth_client, accountant):
    from apps.eventlog.models import EventLog
    from apps.orders.models import Payment

    order = _order(status="shipped")
    payment_id = _pay(auth_client, accountant, order, "500.00")

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment_id}/reopen/")

    assert response.status_code == 200
    payment = Payment.objects.get(pk=payment_id)
    order.refresh_from_db()
    assert payment.status == "received"
    assert payment.confirmed_by is None
    assert payment.confirmed_at is None
    assert order.paid_total == Decimal("0")
    assert order.payment_status == "unpaid"
    event = EventLog.objects.filter(
        event_type="payment", payload__payment_id=payment_id,
        payload__action="reopened",
    ).get()
    assert event.user == accountant


def test_only_confirmed_payment_can_be_reopened(auth_client, accountant):
    from apps.orders.services import create_client_payment

    order = _order(status="shipped")
    payment = create_client_payment(
        order, "cash", order.client.user, amount="100.00",
    )

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment.id}/reopen/")

    assert response.status_code == 400
    assert response.data["code"] == "invalid_payment_stage"


def test_rejected_payment_can_be_restored(
        auth_client, accountant):
    from apps.eventlog.models import EventLog
    from apps.orders.models import Payment
    from apps.orders.services import create_client_payment

    order = _order(status="shipped")
    payment = create_client_payment(
        order, "cash", order.client.user, amount="100.00",
    )
    payment_id = payment.id
    rejected = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment_id}/reject/")
    assert rejected.status_code == 200

    restored = auth_client(accountant).post(
        f"/api/payment-transactions/{payment_id}/restore/")

    assert restored.status_code == 200
    assert restored.data["can_restore"] is False
    payment = Payment.objects.get(pk=payment_id)
    assert payment.status == "requested"
    assert EventLog.objects.filter(
        event_type="payment",
        payload__payment_id=payment_id,
        payload__action="restored",
    ).exists()


def test_only_rejected_payment_can_be_restored(auth_client, accountant):
    order = _order(status="shipped")
    created = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"amount": "100.00"},
        format="json",
    )

    response = auth_client(accountant).post(
        f"/api/payment-transactions/{created.data['id']}/restore/")

    assert response.status_code == 400
    assert response.data["code"] == "invalid_payment_stage"


def test_payment_not_counted_before_confirm():
    """Заявка из клиентского портала не считается полученными деньгами."""
    from apps.orders.services import create_client_payment

    o = _order(status="shipped")  # total 500
    payment = create_client_payment(o, "cash", o.client.user)

    assert payment.status == "requested"
    o.refresh_from_db()
    assert o.paid_total == Decimal("0")
    assert o.payment_status == "unpaid"


def test_payment_note_saved_and_returned(auth_client, accountant):
    """Примечание бухгалтера сохраняется и отдаётся сериализатором."""
    o = _order(status="shipped")
    resp = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/",
        {"amount": "100.00", "method": "cash", "note": "принял кассир"},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.data["note"] == "принял кассир"
    assert resp.data["method"] == "cash"


def test_payment_without_method_is_cash_in_order_card(
        auth_client, accountant, payment_recorder):
    order = _order(status="shipped")

    created = auth_client(payment_recorder).post(
        f"/api/orders/{order.id}/payments/", {"amount": "50"}, format="json",
    )

    assert created.status_code == 201
    card = auth_client(accountant).get(f"/api/orders/{order.id}/")
    assert card.status_code == 200
    assert card.data["pending_payments"] == []
    assert len(card.data["payments"]) == 1
    assert card.data["payments"][0]["amount"] == "50.00"
    assert card.data["payments"][0]["method_label"] == "Наличные"


def test_portal_cash_request_is_received_and_confirmed_in_one_action(
        auth_client, accountant):
    from apps.orders.services import create_client_payment

    order = _order(status="shipped")
    payment = create_client_payment(
        order, "cash", order.client.user, amount="100.00"
    )

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment.id}/receive/"
    )

    assert response.status_code == 200
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == "confirmed"
    assert payment.received_by_id == accountant.id
    assert payment.confirmed_by_id == accountant.id
    assert order.paid_total == Decimal("100.00")


def test_receive_and_confirm_requires_payments_confirm(
        auth_client, payment_recorder):
    from apps.orders.services import create_client_payment

    order = _order(status="shipped")
    payment = create_client_payment(
        order, "cash", order.client.user, amount="100.00"
    )

    response = auth_client(payment_recorder).post(
        f"/api/orders/{order.id}/payments/{payment.id}/receive/"
    )

    assert response.status_code == 403
    payment.refresh_from_db()
    assert payment.status == "requested"


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


def test_cash_prepayment_before_shipped_is_settled(auth_client, accountant):
    o = _order(status="arrived")
    resp = auth_client(accountant).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    assert resp.status_code == 201, resp.data
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.payment_status == "settled"


def test_manager_cannot_record_payment(auth_client, manager):
    o = _order(status="shipped")
    resp = auth_client(manager).post(
        f"/api/orders/{o.id}/payments/", {"amount": "500.00"}, format="json"
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "amount",
    [
        "not-a-number",
        "NaN",
        "-1",
        "0.001",
        "1E-1000000000",
        "10000000000",
    ],
)
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
