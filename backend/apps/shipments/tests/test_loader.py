from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment, WaybillSettings
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("loader", codes=["loader.view", "loader.confirm"])


@pytest.fixture
def product(boss):
    item = Product.objects.create(name="Д1с", color="Red", weight_kg="50")
    receive_stock(item, 100, boss)
    return item


def _order(product, status="confirmed", quantity=2, **fields):
    client = Client.objects.create_with_user(first_name="Мурат", phone="+7 (778) 535-22-10", company_name="ИП Мурат")
    order = Order.objects.create(client=client, status=status, **fields)
    OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price="10000.00")
    return order


def _bags(product):
    return sum(StockItem.objects.filter(product=product).values_list("bags", flat=True))


def test_queue_shows_orders_waiting_for_shipment(auth_client, loader, product):
    waiting = _order(product)
    on_post = _order(product, status="arrived", transport_type="train")
    _order(product, status="pending")
    shipped = _order(product, status="shipped")

    rows = auth_client(loader).get("/api/loader/queue/").data

    assert [row["id"] for row in rows] == [waiting.pk, on_post.pk]
    assert rows[0]["client_name"] == "ИП Мурат"
    assert rows[0]["bags"] == 2
    assert rows[0]["total_kg"] == "100.00"
    assert rows[0]["total_amount"] == "20000.00"
    # Оплата видна сразу: клиент мог заплатить заранее, грузчик это знает.
    assert rows[0]["payment_status"] == "unpaid"
    assert rows[0]["paid_total"] == "0.00"
    assert rows[0]["remaining_amount"] == "20000.00"
    assert shipped.pk not in [row["id"] for row in rows]


def test_queue_filters_by_planned_day_and_search(auth_client, loader, product):
    tomorrow = timezone.localdate() + timedelta(days=1)
    today_order = _order(product)
    tomorrow_order = _order(product, arrival_date=tomorrow, truck_number="612BEX13")
    api = auth_client(loader)

    assert [row["id"] for row in api.get(f"/api/loader/queue/?day={tomorrow}").data] == [tomorrow_order.pk]
    assert [row["id"] for row in api.get("/api/loader/queue/?search=612").data] == [tomorrow_order.pk]
    assert today_order.pk in [row["id"] for row in api.get("/api/loader/queue/").data]


def test_queue_separates_overdue_from_today(auth_client, loader, product):
    today = timezone.localdate()
    fresh = _order(product, arrival_date=today)
    late = _order(product, arrival_date=today - timedelta(days=30))
    api = auth_client(loader)

    assert [row["id"] for row in api.get("/api/loader/queue/?overdue=1").data] == [late.pk]
    assert [row["id"] for row in api.get(f"/api/loader/queue/?day={today}").data] == [fresh.pk]
    assert {row["id"] for row in api.get("/api/loader/queue/").data} == {fresh.pk, late.pk}


def test_one_button_ships_ordered_quantity_and_prints_waybill(auth_client, loader, product):
    order = _order(product, quantity=3)
    api = auth_client(loader)

    response = api.post(f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 bjn 13"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.payment_status == "unpaid"
    assert order.truck_number == "403 BJN 13"
    assert Shipment.objects.get(order=order).bags_loaded == 3
    assert _bags(product) == 97
    assert EventLog.objects.filter(order=order, event_type="shipment", message__contains=f"№{order.pk}").exists()

    history = api.get("/api/loader/history/").data
    assert [row["id"] for row in history] == [order.pk]

    waybill = api.get(f"/api/loader/orders/{order.pk}/waybill/")
    assert waybill.status_code == 200
    assert waybill["Content-Type"] == "application/pdf"
    assert waybill.content.startswith(b"%PDF")


def test_dispatch_is_single_and_only_for_waiting_orders(auth_client, loader, product):
    pending = _order(product, status="pending")
    order = _order(product)
    api = auth_client(loader)

    assert api.post(f"/api/loader/orders/{pending.pk}/dispatch/", {}, format="json").status_code == 400
    assert api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json").status_code == 200
    # Повторное нажатие не списывает склад второй раз.
    assert api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json").status_code == 400
    assert _bags(product) == 98
    assert api.get(f"/api/loader/orders/{pending.pk}/waybill/").status_code == 400


def test_history_filters_by_shipment_date(auth_client, loader, product):
    order = _order(product)
    api = auth_client(loader)
    api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json")
    yesterday = timezone.localdate() - timedelta(days=1)

    assert api.get(f"/api/loader/history/?date_from={yesterday}&date_to={yesterday}").data == []


def test_confirm_requires_loader_confirm_permission(auth_client, user_with_perms, product):
    viewer = user_with_perms("loader-viewer", codes=["loader.view"])
    order = _order(product)
    api = auth_client(viewer)

    assert api.get("/api/loader/queue/").status_code == 200
    assert api.post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json").status_code == 403
    assert auth_client(user_with_perms("stranger", codes=["orders.view"])).get("/api/loader/queue/").status_code == 403


def test_waybill_settings_are_read_by_loader_and_changed_by_admin(auth_client, loader, user_with_perms):
    admin = user_with_perms("rights-admin", codes=["sys_permissions.manage"])
    payload = {"point_name": "мельница Аксу", "signers": [{"role": "Кладовщик", "name": "Тажи А"}]}

    assert auth_client(loader).get("/api/loader/waybill-settings/").data["signers"][0]["name"] == "Егамбердиева Д"
    assert auth_client(loader).put("/api/loader/waybill-settings/", payload, format="json").status_code == 403

    response = auth_client(admin).put("/api/loader/waybill-settings/", payload, format="json")

    assert response.status_code == 200
    assert WaybillSettings.load().signers == [{"role": "Кладовщик", "name": "Тажи А"}]


def test_dispatch_closes_open_ai_counting_with_its_bags_then_ships(auth_client, loader, product, monkeypatch):
    """Кнопок погрузки в Моноблоке нет: открытый AI-подсчёт закрывает сама отгрузка."""
    from apps.cameras import counting
    from apps.cameras.models import AiCountingSession
    from apps.shipments.services import finish_ai_counting

    order = _order(product, status="loading", quantity=5)
    Shipment.objects.create(order=order)
    session = AiCountingSession.objects.create(order=order, camera="cam2", status=AiCountingSession.ACTIVE)
    calls = []

    def fake_stop(camera, stopped_order, user, *, complete_order, expected_session_id, dispatching):
        calls.append((camera, complete_order, expected_session_id, dispatching))
        finish_ai_counting(stopped_order, 4, user)
        AiCountingSession.objects.filter(pk=session.pk).update(status=AiCountingSession.CLOSED)
        return {}

    monkeypatch.setattr(counting, "stop", fake_stop)
    response = auth_client(loader).post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json")

    assert response.status_code == 200, response.data
    assert calls == [("cam2", True, session.pk, True)]
    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.shipment.bags_loaded == 4


def test_dispatch_keeps_order_when_camera_pc_is_unreachable(auth_client, loader, product, monkeypatch):
    from apps.cameras import ai, counting
    from apps.cameras.models import AiCountingSession

    order = _order(product, status="loading")
    AiCountingSession.objects.create(order=order, camera="cam2", status=AiCountingSession.ACTIVE)

    def unreachable(*args, **kwargs):
        raise ai.AiUnavailable("down")

    monkeypatch.setattr(counting, "stop", unreachable)
    response = auth_client(loader).post(f"/api/loader/orders/{order.pk}/dispatch/", {}, format="json")

    assert response.status_code == 502
    assert response.data["code"] == "ai_unavailable"
    order.refresh_from_db()
    assert order.status == "loading"
    assert _bags(product) == 100


def test_loader_undoes_own_fresh_dispatch(auth_client, loader, product):
    order = _order(product, quantity=3)
    api = auth_client(loader)
    api.post(f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 BJN 13"}, format="json")
    before = _bags(product)

    history = api.get("/api/loader/history/").data
    assert [row["can_rollback"] for row in history] == [True]

    response = api.post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order.status == "confirmed"
    # Мешки вернулись на склад, а заказ снова ждёт отгрузки.
    assert _bags(product) == before + 3
    assert [row["id"] for row in api.get("/api/loader/queue/").data] == [order.pk]
    assert EventLog.objects.filter(order=order, event_type="shipment_rollback").exists()


def test_loader_cannot_undo_an_old_or_foreign_dispatch(auth_client, loader, user_with_perms, product):
    order = _order(product)
    api = auth_client(loader)
    api.post(f"/api/loader/orders/{order.pk}/dispatch/", {"truck_number": "403 BJN 13"}, format="json")

    # Чужая отгрузка: другой грузчик её не отменяет.
    other = user_with_perms("loader-2", codes=["loader.view", "loader.confirm"])
    foreign = auth_client(other).post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")
    assert foreign.status_code == 400
    assert foreign.data["code"] == "rollback_not_allowed"
    assert auth_client(other).get("/api/loader/history/").data[0]["can_rollback"] is False

    # Своя, но старше часа: дальше откат оформляет старший в «Заказах».
    shipment = Shipment.objects.get(order=order)
    shipment.shipped_at = timezone.now() - timedelta(hours=2)
    shipment.save(update_fields=["shipped_at"])
    late = api.post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")
    assert late.status_code == 400
    assert "часа" in late.data["detail"]
    order.refresh_from_db()
    assert order.status == "shipped"
