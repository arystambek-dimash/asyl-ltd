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
    assert shipped.pk not in [row["id"] for row in rows]


def test_queue_filters_by_planned_day_and_search(auth_client, loader, product):
    tomorrow = timezone.localdate() + timedelta(days=1)
    today_order = _order(product)
    tomorrow_order = _order(product, arrival_date=tomorrow, truck_number="612BEX13")
    api = auth_client(loader)

    assert [row["id"] for row in api.get(f"/api/loader/queue/?day={tomorrow}").data] == [tomorrow_order.pk]
    assert [row["id"] for row in api.get("/api/loader/queue/?search=612").data] == [tomorrow_order.pk]
    assert today_order.pk in [row["id"] for row in api.get("/api/loader/queue/").data]


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
