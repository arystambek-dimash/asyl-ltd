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
    OrderItem.objects.create(order=o, product=prod, quantity=qty, unit_price=price)
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


def test_confirmed_payment_can_be_reopened_with_audit_log(auth_client, accountant):
    from apps.eventlog.models import EventLog
    from apps.orders.models import Payment

    order = _order(status="shipped")
    payment_id = _pay_through_chain(auth_client, accountant, order, "500.00")

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
    order = _order(status="shipped")
    created = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/", {"amount": "100.00"}, format="json")

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{created.data['id']}/reopen/")

    assert response.status_code == 400
    assert response.data["code"] == "invalid_payment_stage"


def test_cashier_log_marks_only_current_confirmation_as_reopenable(
        auth_client, accountant):
    order = _order(status="shipped")
    payment_id = _pay_through_chain(auth_client, accountant, order, "500.00")

    before = auth_client(accountant).get("/api/orders/cashier-log/")

    assert before.status_code == 200
    confirmation = next(
        row for row in before.data
        if row["payload"].get("payment_id") == payment_id
        and row["payload"].get("payment_stage") == "confirmed"
    )
    assert confirmation["can_reopen"] is True

    auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment_id}/reopen/")
    after = auth_client(accountant).get("/api/orders/cashier-log/")
    same_confirmation = next(row for row in after.data if row["id"] == confirmation["id"])
    assert same_confirmation["can_reopen"] is False
    assert any(row["payload"].get("action") == "reopened" for row in after.data)

    auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment_id}/confirm/")
    reconfirmed = auth_client(accountant).get("/api/orders/cashier-log/")
    reopenable = [
        row for row in reconfirmed.data
        if row["payload"].get("payment_id") == payment_id and row["can_reopen"]
    ]
    assert len(reopenable) == 1


def test_rejected_payment_can_be_restored_from_cashier_log(
        auth_client, accountant):
    from apps.eventlog.models import EventLog
    from apps.orders.models import Payment

    order = _order(status="shipped")
    created = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"amount": "100.00", "method": "cash"},
        format="json",
    )
    payment_id = created.data["id"]
    rejected = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment_id}/reject/")
    assert rejected.status_code == 200

    journal = auth_client(accountant).get("/api/orders/cashier-log/")
    rejection = next(
        row for row in journal.data
        if row["payload"].get("payment_id") == payment_id
        and row["payload"].get("payment_stage") == "rejected"
    )
    assert rejection["can_restore"] is True

    restored = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{payment_id}/restore/")

    assert restored.status_code == 200
    payment = Payment.objects.get(pk=payment_id)
    assert payment.status == "received"
    assert EventLog.objects.filter(
        event_type="payment",
        payload__payment_id=payment_id,
        payload__action="restored",
    ).exists()
    journal_after = auth_client(accountant).get("/api/orders/cashier-log/")
    same_rejection = next(
        row for row in journal_after.data if row["id"] == rejection["id"]
    )
    assert same_rejection["can_restore"] is False


def test_only_rejected_payment_can_be_restored(auth_client, accountant):
    order = _order(status="shipped")
    created = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"amount": "100.00"},
        format="json",
    )

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/{created.data['id']}/restore/")

    assert response.status_code == 400
    assert response.data["code"] == "invalid_payment_stage"


def test_cashier_log_hides_provider_name_in_historical_messages(
        auth_client, accountant):
    from apps.eventlog.models import EventLog

    order = _order(status="shipped")
    event = EventLog.objects.create(
        event_type="payment",
        message="Счёт ApiPay №84 создан; клиент инициировал оплату (invoice)",
        user=accountant,
        order=order,
    )

    response = auth_client(accountant).get("/api/orders/cashier-log/")

    row = next(item for item in response.data if item["id"] == event.id)
    assert "ApiPay" not in row["message"]
    assert "Счёт на оплату" in row["message"]
    assert "(счёт на оплату)" in row["message"]


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


def test_mixed_payment_is_created_atomically(auth_client, accountant):
    order = _order(status="shipped")

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "cash", "amount": "125.00"},
            {"method": "kaspi", "amount": "200.00"},
            {"method": "invoice", "amount": "175.00"},
        ], "note": "смешанная оплата"},
        format="json",
    )

    assert response.status_code == 201
    assert {row["method"] for row in response.data} == {"cash", "kaspi", "invoice"}
    assert all(row["status"] == "received" for row in response.data)
    assert order.payments.count() == 3
    assert {payment.note for payment in order.payments.all()} == {"смешанная оплата"}


def test_mixed_payment_cannot_exceed_unreserved_balance(auth_client, accountant):
    order = _order(status="shipped")
    first = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"amount": "100.00", "method": "cash"}, format="json")
    assert first.status_code == 201

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "kaspi", "amount": "250.00"},
            {"method": "invoice", "amount": "151.00"},
        ]}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "payment_exceeds_remaining"
    assert order.payments.count() == 1


def test_mixed_payment_rejects_duplicate_method_without_partial_write(
        auth_client, accountant):
    order = _order(status="shipped")

    response = auth_client(accountant).post(
        f"/api/orders/{order.id}/payments/",
        {"parts": [
            {"method": "cash", "amount": "100.00"},
            {"method": "cash", "amount": "100.00"},
        ]}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "duplicate_payment_method"
    assert not order.payments.exists()


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
