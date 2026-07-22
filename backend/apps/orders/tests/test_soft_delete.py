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
from rest_framework.test import APIClient
from apps.cameras.models import AiCountingSession
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders import services as order_services
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _product():
    return Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")


def _order(client, product, qty=2, status="shipped", payment_status="unpaid",
           paid=None, store=None):
    order = Order.objects.create(
        client=client, store=store, status=status, payment_status=payment_status)
    OrderItem.objects.create(
        order=order, product=product, quantity=qty, unit_price="100.00")
    if paid is not None:
        Payment.objects.create(order=order, amount=paid, status="confirmed")
    return order


# ── Механика корзины ──────────────────────────────────────────────────────

def test_delete_moves_to_trash_not_gone(manager):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    r = _api(manager).delete(f"/api/orders/{o.id}/")
    assert r.status_code == 204
    # Физически заказ на месте, но помечен удалённым.
    o.refresh_from_db()
    assert o.deleted_at is not None
    assert Order.all_objects.filter(pk=o.pk).exists()
    # Из «живого» менеджера исчез.
    assert not Order.objects.filter(pk=o.pk).exists()


def test_deleted_order_hidden_from_list(manager):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o1 = _order(c, p)
    o2 = _order(c, p)
    _api(manager).delete(f"/api/orders/{o2.id}/")
    ids = [row["id"] for row in _api(manager).get("/api/orders/").data]
    assert o1.id in ids
    assert o2.id not in ids


def test_trash_lists_deleted_and_restore_brings_back(manager):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    _api(manager).delete(f"/api/orders/{o.id}/")

    trash = _api(manager).get("/api/orders/trash/")
    assert trash.status_code == 200
    assert [row["id"] for row in trash.data] == [o.id]

    r = _api(manager).post(f"/api/orders/{o.id}/restore/")
    assert r.status_code == 200
    assert r.data["deleted_at"] is None
    assert r.data["deleted_by_name"] is None
    o.refresh_from_db()
    assert o.deleted_at is None
    # Снова в списке, в корзине пусто.
    assert o.id in [row["id"] for row in _api(manager).get("/api/orders/").data]
    assert _api(manager).get("/api/orders/trash/").data == []


def test_restore_of_live_order_fails(manager):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    r = _api(manager).post(f"/api/orders/{o.id}/restore/")
    assert r.status_code == 400  # не в корзине


def test_editor_purge_deletes_non_financial_draft_from_trash(manager):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = _order(c, p, status="draft")

    # Живой заказ навсегда не удалить — сначала корзина.
    assert _api(manager).delete(f"/api/orders/{o.id}/purge/").status_code == 400

    _api(manager).delete(f"/api/orders/{o.id}/")
    r = _api(manager).delete(f"/api/orders/{o.id}/purge/")
    assert r.status_code == 204
    # Не проведённый черновик исчез совсем вместе с позициями.
    assert not Order.all_objects.filter(pk=o.id).exists()
    assert _api(manager).get("/api/orders/trash/").data == []


def test_purge_preserves_shipped_financial_records(manager):
    product = _product()
    client = Client.objects.create(first_name="A", last_name="B", phone="1")
    order = _order(client, product, paid="100.00")
    _api(manager).delete(f"/api/orders/{order.id}/")

    response = _api(manager).delete(f"/api/orders/{order.id}/purge/")

    assert response.status_code == 400
    assert response.data["code"] == "financial_record_protected"
    assert Order.all_objects.filter(pk=order.id).exists()
    assert Payment.objects.filter(order_id=order.id).exists()


def test_purge_preserves_ai_history(manager):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = _order(c, p, status="draft")
    AiCountingSession.objects.create(
        order=o,
        camera="cam2",
        status=AiCountingSession.CLOSED,
    )
    _api(manager).delete(f"/api/orders/{o.id}/")

    response = _api(manager).delete(f"/api/orders/{o.id}/purge/")

    assert response.status_code == 400
    assert response.data["code"] == "financial_record_protected"
    assert Order.all_objects.filter(pk=o.pk).exists()


def test_purge_requires_edit_permission(operator):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = _order(c, p, status="draft")
    Order.all_objects.filter(pk=o.pk).update(deleted_at="2026-07-16T00:00:00Z")
    assert _api(operator).delete(f"/api/orders/{o.id}/purge/").status_code == 403


@pytest.mark.django_db(transaction=True)
def test_purge_rechecks_order_after_concurrent_restore(manager):
    product = _product()
    client = Client.objects.create(first_name="A", last_name="B", phone="race")
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

def test_deleted_order_excluded_from_client_debts(manager, boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    _order(c, p, qty=3)                       # долг 300 — остаётся
    doomed = _order(c, p, qty=5)              # долг 500 — удалим
    _api(manager).delete(f"/api/orders/{doomed.id}/")

    r = _api(boss).get("/api/clients/debts/")
    row = next((x for x in r.data if x["client_id"] == c.id), None)
    assert row is not None
    assert row["debt_total"] == "300.00"      # без удалённого
    assert row["orders_count"] == 1


def test_deleted_order_excluded_from_store_debts(manager, boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    store = Store.objects.create(client=c, name="S",
                                 payment_schedule_type="monthly", payment_days=[25])
    _order(c, p, qty=2, store=store)          # 200 остаётся
    doomed = _order(c, p, qty=4, store=store)  # 400 удалим
    _api(manager).delete(f"/api/orders/{doomed.id}/")

    r = _api(boss).get("/api/stores/debts/")
    row = next((x for x in r.data if x["store_id"] == store.id), None)
    assert row is not None
    assert Decimal(row["debt_total"]) == Decimal("200.00")


def test_deleted_order_excluded_from_client_history(manager, boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    kept = _order(c, p, qty=2, paid="200.00", payment_status="settled")  # выручка 200
    doomed = _order(c, p, qty=5)                                          # 500 удалим
    _api(manager).delete(f"/api/orders/{doomed.id}/")

    r = _api(boss).get(f"/api/clients/{c.id}/history/")
    assert r.status_code == 200
    assert Decimal(r.data["summary"]["revenue"]) == Decimal("200.00")
    assert Decimal(r.data["summary"]["debt"]) == Decimal("0")
    # Удалённый заказ и его оплаты не видны ни в продажах, ни в погашениях.
    assert [row["id"] for row in r.data["sales"]] == [kept.id]
    assert all(row["order_id"] == kept.id for row in r.data["payments"])


def test_deleted_order_excluded_from_debts_endpoint(manager, boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    keep = _order(c, p, qty=2)
    doomed = _order(c, p, qty=3)
    _api(manager).delete(f"/api/orders/{doomed.id}/")
    rows = {row["client_id"]: row for row in _api(boss).get("/api/clients/debts/").data}
    # Удалённый заказ не участвует в долге: остался только keep (2 × 100).
    assert rows[c.id]["orders_count"] == 1
    assert rows[c.id]["debt_total"] == str(keep.total_amount)


def test_deleted_order_not_in_payments_queue(manager, accountant):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = _order(c, p)
    Payment.objects.create(order=o, amount="100", status="received")
    _api(manager).delete(f"/api/orders/{o.id}/")
    r = _api(accountant).get("/api/orders/payments-queue/?stage=received")
    assert o.id not in [row["order"] for row in r.data]
