"""Soft-delete заказов: корзина, восстановление и — самое важное —
удалённый заказ НЕ влияет ни на один отчёт/агрегат."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from decimal import Decimal
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from rest_framework.exceptions import ValidationError
from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.orders import services as order_services
from apps.orders.models import Order, OrderItem, Payment, StatusChangeRequest
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


def _order(client, product, qty=2, status="shipped", payment_status="unpaid",
           paid=None):
    order = Order.objects.create(
        client=client, status=status, payment_status=payment_status)
    OrderItem.objects.create(
        order=order, product=product, quantity=qty, unit_price="100.00")
    if paid is not None:
        Payment.objects.create(order=order, amount=paid, status="confirmed")
    return order


# ── Механика корзины ──────────────────────────────────────────────────────

def test_delete_moves_to_trash_not_gone(manager, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    r = auth_client(manager).delete(f"/api/orders/{o.id}/")
    assert r.status_code == 204
    # Физически заказ на месте, но помечен удалённым.
    o.refresh_from_db()
    assert o.deleted_at is not None
    assert Order.all_objects.filter(pk=o.pk).exists()
    # Из «живого» менеджера исчез.
    assert not Order.objects.filter(pk=o.pk).exists()


def test_delete_cannot_hide_order_with_open_ai_session(manager, make_product, auth_client):
    product = make_product()
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="open-ai-delete",
    )
    order = _order(client, product, status="confirmed")
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=manager,
    )

    response = auth_client(manager).delete(f"/api/orders/{order.id}/")

    assert response.status_code == 400
    assert response.data["code"] == "ai_session_active"
    order.refresh_from_db()
    session.refresh_from_db()
    assert order.deleted_at is None
    assert session.status == AiCountingSession.STARTING


@pytest.mark.parametrize("status", ["arrived", "loading", "loaded"])
def test_delete_cannot_hide_active_loading_without_ai(manager, status, make_product, auth_client):
    product = make_product()
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone=f"active-delete-{status}",
    )
    order = _order(client, product, status=status)

    response = auth_client(manager).delete(f"/api/orders/{order.id}/")

    assert response.status_code == 400
    assert response.data["code"] == "active_loading"
    order.refresh_from_db()
    assert order.deleted_at is None


def test_deleted_order_hidden_from_list(manager, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o1 = _order(c, p)
    o2 = _order(c, p)
    auth_client(manager).delete(f"/api/orders/{o2.id}/")
    ids = [row["id"] for row in auth_client(manager).get("/api/orders/").data]
    assert o1.id in ids
    assert o2.id not in ids


def test_trash_lists_deleted_and_restore_brings_back(manager, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    auth_client(manager).delete(f"/api/orders/{o.id}/")

    trash = auth_client(manager).get("/api/orders/trash/")
    assert trash.status_code == 200
    assert [row["id"] for row in trash.data] == [o.id]

    r = auth_client(manager).post(f"/api/orders/{o.id}/restore/")
    assert r.status_code == 200
    assert r.data["deleted_at"] is None
    assert r.data["deleted_by_name"] is None
    o.refresh_from_db()
    assert o.deleted_at is None
    # Снова в списке, в корзине пусто.
    assert o.id in [row["id"] for row in auth_client(manager).get("/api/orders/").data]
    assert auth_client(manager).get("/api/orders/trash/").data == []


def test_trash_preview_is_bounded_and_reports_full_count(manager, make_product, auth_client):
    product = make_product()
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    orders = [_order(client, product, status="draft") for _ in range(6)]
    for order in orders:
        auth_client(manager).delete(f"/api/orders/{order.id}/")

    response = auth_client(manager).get("/api/orders/trash-preview/")

    assert response.status_code == 200
    assert response.data["count"] == 6
    assert len(response.data["results"]) == 4
    assert response.data["results"][0]["id"] == orders[-1].id


def test_restore_of_live_order_fails(manager, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    r = auth_client(manager).post(f"/api/orders/{o.id}/restore/")
    assert r.status_code == 400  # не в корзине


def test_editor_purge_deletes_non_financial_draft_from_trash(manager, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = _order(c, p, status="draft")

    # Живой заказ навсегда не удалить — сначала корзина.
    assert auth_client(manager).delete(f"/api/orders/{o.id}/purge/").status_code == 400

    auth_client(manager).delete(f"/api/orders/{o.id}/")
    r = auth_client(manager).delete(f"/api/orders/{o.id}/purge/")
    assert r.status_code == 204
    # Не проведённый черновик исчез совсем вместе с позициями.
    assert not Order.all_objects.filter(pk=o.id).exists()
    assert auth_client(manager).get("/api/orders/trash/").data == []


def test_purge_hides_shipped_order_but_preserves_financial_records(manager, make_product, auth_client):
    product = make_product()
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    order = _order(client, product, paid="100.00")
    auth_client(manager).delete(f"/api/orders/{order.id}/")

    response = auth_client(manager).delete(f"/api/orders/{order.id}/purge/")

    assert response.status_code == 204
    retained = Order.all_objects.get(pk=order.id)
    assert retained.purged_at is not None
    assert retained.purged_by == manager
    assert not Order.all_objects.deleted().filter(pk=order.id).exists()
    assert order.id not in [
        row["id"] for row in auth_client(manager).get("/api/orders/trash/").data
    ]
    assert Payment.objects.filter(order_id=order.id).exists()


def test_purge_hides_order_but_preserves_ai_history(manager, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = _order(c, p, status="draft")
    AiCountingSession.objects.create(
        order=o,
        camera="cam2",
        status=AiCountingSession.CLOSED,
    )
    auth_client(manager).delete(f"/api/orders/{o.id}/")

    response = auth_client(manager).delete(f"/api/orders/{o.id}/purge/")

    assert response.status_code == 204
    retained = Order.all_objects.get(pk=o.pk)
    assert retained.purged_at is not None
    assert retained.purged_by == manager
    assert not Order.all_objects.deleted().filter(pk=o.pk).exists()
    assert AiCountingSession.objects.filter(order_id=o.pk).exists()


def test_purge_hides_order_but_preserves_shipment_history(manager, make_product, auth_client):
    product = make_product()
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="shipment-history",
    )
    order = _order(client, product, status="draft")
    shipment = Shipment.objects.create(order=order, bags_loaded=2)
    auth_client(manager).delete(f"/api/orders/{order.id}/")

    response = auth_client(manager).delete(f"/api/orders/{order.id}/purge/")

    assert response.status_code == 204
    assert Order.all_objects.get(pk=order.pk).purged_at is not None
    assert Shipment.objects.filter(pk=shipment.pk, order_id=order.pk).exists()


def test_purge_retains_non_draft_business_document_without_relations(manager, make_product, auth_client):
    product = make_product()
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="pending-business-document",
    )
    order = _order(client, product, status="pending")
    auth_client(manager).delete(f"/api/orders/{order.id}/")

    response = auth_client(manager).delete(f"/api/orders/{order.id}/purge/")

    assert response.status_code == 204
    retained = Order.all_objects.get(pk=order.pk)
    assert retained.status == "pending"
    assert retained.purged_at is not None
    assert not Order.all_objects.deleted().filter(pk=order.pk).exists()


def test_purge_retains_draft_with_status_approval_history(manager, make_product, auth_client):
    product = make_product()
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="approval-history",
    )
    order = _order(client, product, status="draft")
    request = StatusChangeRequest.objects.create(
        order=order,
        to_status="cancelled",
        requested_by=manager,
    )
    auth_client(manager).delete(f"/api/orders/{order.id}/")

    response = auth_client(manager).delete(f"/api/orders/{order.id}/purge/")

    assert response.status_code == 204
    assert Order.all_objects.get(pk=order.pk).purged_at is not None
    assert StatusChangeRequest.objects.filter(pk=request.pk).exists()


def test_purged_business_document_cannot_be_restored(manager, make_product):
    product = make_product()
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="purged-restore",
    )
    order = _order(client, product, status="pending")
    order_services.soft_delete_order(order, manager)
    order_services.purge_order(order, manager)
    retained = Order.all_objects.get(pk=order.pk)

    with pytest.raises(ValidationError) as exc_info:
        order_services.restore_order(retained, manager)

    assert exc_info.value.detail["code"] == "already_purged"
    retained.refresh_from_db()
    assert retained.deleted_at is not None
    assert retained.purged_at is not None


def test_purge_requires_edit_permission(operator, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = _order(c, p, status="draft")
    Order.all_objects.filter(pk=o.pk).update(deleted_at="2026-07-16T00:00:00Z")
    assert auth_client(operator).delete(f"/api/orders/{o.id}/purge/").status_code == 403


@pytest.mark.django_db(transaction=True)
def test_purge_rechecks_order_after_concurrent_restore(manager, make_product):
    product = make_product()
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="race")
    order = _order(client, product, status="draft")
    order_services.soft_delete_order(order, manager)
    stale_for_purge = Order.all_objects.get(pk=order.pk)

    restore_holds_lock = Event()
    release_restore = Event()
    purge_started = Event()
    original_log_event = order_services.log_event

    def coordinated_log_event(event_type, message, **kwargs):
        if message == "Заказ восстановлен из корзины":
            restore_holds_lock.set()
            assert release_restore.wait(timeout=5)
        return original_log_event(event_type, message, **kwargs)

    def restore():
        close_old_connections()
        try:
            local_order = Order.all_objects.get(pk=order.pk)
            local_user = get_user_model().objects.get(pk=manager.pk)
            order_services.restore_order(local_order, local_user)
        finally:
            close_old_connections()

    def purge():
        close_old_connections()
        try:
            local_user = get_user_model().objects.get(pk=manager.pk)
            purge_started.set()
            try:
                order_services.purge_order(stale_for_purge, local_user)
            except ValidationError as exc:
                return str(exc.detail["code"])
            return "purged"
        finally:
            close_old_connections()

    with patch.object(
        order_services, "log_event", side_effect=coordinated_log_event,
    ), ThreadPoolExecutor(max_workers=2) as executor:
        restore_future = executor.submit(restore)
        try:
            assert restore_holds_lock.wait(timeout=5)
            purge_future = executor.submit(purge)
            assert purge_started.wait(timeout=5)
        finally:
            release_restore.set()
        restore_future.result(timeout=5)
        assert purge_future.result(timeout=5) == "not_deleted"

    order.refresh_from_db()
    assert order.deleted_at is None


# ── Удалённый заказ НЕ влияет на отчёты (главное) ─────────────────────────

def test_deleted_order_excluded_from_client_debts(manager, boss, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    _order(c, p, qty=3)                       # долг 300 — остаётся
    doomed = _order(c, p, qty=5)              # долг 500 — удалим
    auth_client(manager).delete(f"/api/orders/{doomed.id}/")

    r = auth_client(boss).get("/api/clients/debts/")
    row = next((x for x in r.data if x["client_id"] == c.id), None)
    assert row is not None
    assert row["debt_total"] == "300.00"      # без удалённого
    assert row["orders_count"] == 1


def test_deleted_order_excluded_from_client_history(manager, boss, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    kept = _order(c, p, qty=2, paid="200.00", payment_status="settled")  # выручка 200
    doomed = _order(c, p, qty=5)                                          # 500 удалим
    auth_client(manager).delete(f"/api/orders/{doomed.id}/")

    r = auth_client(boss).get(f"/api/clients/{c.id}/history/")
    assert r.status_code == 200
    assert Decimal(r.data["summary"]["revenue"]) == Decimal("200.00")
    assert Decimal(r.data["summary"]["debt"]) == Decimal("0")
    # Удалённый заказ и его оплаты не видны ни в продажах, ни в погашениях.
    assert [row["id"] for row in r.data["sales"]] == [kept.id]
    assert all(row["order_id"] == kept.id for row in r.data["payments"])


def test_deleted_order_not_in_payments_queue(manager, accountant, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    Payment.objects.create(order=o, amount="100", status="received")
    auth_client(manager).delete(f"/api/orders/{o.id}/")
    r = auth_client(accountant).get("/api/orders/payments-queue/")
    assert o.id not in [row["order"] for row in r.data]


def test_transactions_journal_keeps_money_of_shipped_order_from_trash(manager, accountant, make_product, auth_client):
    """Корзина прячет отгруженный заказ, но не его деньги: журнал и итог кассы их хранят."""
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    live = _order(c, p, paid="1000.00")
    trashed = _order(c, p, paid="7777.00")
    auth_client(manager).delete(f"/api/orders/{trashed.id}/")

    r = auth_client(accountant).get("/api/payment-transactions/")
    assert {row["order"] for row in r.data["results"]} == {live.id, trashed.id}
    assert r.data["summary"]["paid_by_currency"]["KZT"] == "8777.00"
    assert r.data["summary"]["paid_by_method"]["KZT"]["cash"] == "8777.00"


def test_transactions_journal_skips_unshipped_order_from_trash(manager, accountant, make_product, auth_client):
    p = make_product()
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    live = _order(c, p, paid="1000.00")
    trashed = _order(c, p, status="confirmed")
    Payment.objects.create(order=trashed, amount="500.00", status="rejected")
    auth_client(manager).delete(f"/api/orders/{trashed.id}/")

    r = auth_client(accountant).get("/api/payment-transactions/")
    assert [row["order"] for row in r.data["results"]] == [live.id]
    assert r.data["summary"]["paid_by_currency"]["KZT"] == "1000.00"
