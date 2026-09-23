"""Грузчик: области «Фуры» и «Вагоны» со своими правами.

Страница и кнопка — общие (loader.view / loader.confirm), а какой транспорт
человек видит и отгружает, решает область: loader.trucks или loader.wagons.
"""
from unittest.mock import patch

import pytest
from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from apps.cameras import ai, counting
from apps.cameras.models import AiCountingSession, MonoblockCameraSettings
from apps.cameras.policies import can_control_session
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.shipments import services
from apps.shipments.access import allowed_transports, assert_can_ship
from apps.shipments.models import Shipment
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def trucks_loader(user_with_perms):
    return user_with_perms("trucks-loader", codes=["loader.view", "loader.confirm", "loader.trucks"])


@pytest.fixture
def wagons_loader(user_with_perms):
    return user_with_perms("wagons-loader", codes=["loader.view", "loader.confirm", "loader.wagons"])


@pytest.fixture
def product(boss):
    item = Product.objects.create(name="Д1с", color="Red", weight_kg="50")
    receive_stock(item, 100, boss)
    return item


def _order(product, transport_type="truck", status="confirmed", **fields):
    client = Client.objects.create_with_user(first_name="Мурат", phone="+7 (778) 535-22-10", company_name="ИП Мурат")
    order = Order.objects.create(client=client, status=status, transport_type=transport_type, **fields)
    OrderItem.objects.create(order=order, product=product, quantity=2, unit_price="10000.00")
    return order


def test_areas_decide_the_transport_a_loader_sees(user_with_perms, trucks_loader, wagons_loader, django_user_model):
    both = user_with_perms("both-loader", codes=["loader.view", "loader.trucks", "loader.wagons"])
    root = django_user_model.objects.create_superuser(username="loader-root", password="pass12345")

    assert allowed_transports(trucks_loader) == ("truck",)
    assert allowed_transports(wagons_loader) == ("train",)
    assert allowed_transports(both) == ("truck", "train")
    assert allowed_transports(root) == ("truck", "train")
    assert allowed_transports(user_with_perms("page-only", codes=["loader.view"])) == ()


def test_queue_and_history_show_only_the_loaders_area(auth_client, trucks_loader, wagons_loader, product):
    truck = _order(product)
    wagon = _order(product, transport_type="train")

    assert [row["id"] for row in auth_client(trucks_loader).get("/api/loader/queue/").data] == [truck.pk]
    assert [row["id"] for row in auth_client(wagons_loader).get("/api/loader/queue/").data] == [wagon.pk]

    auth_client(trucks_loader).post(
        f"/api/loader/orders/{truck.pk}/dispatch/", {"truck_number": "403BJN13"}, format="json")
    auth_client(wagons_loader).post(
        f"/api/loader/orders/{wagon.pk}/dispatch/", {"truck_number": "00123456"}, format="json")

    assert [row["id"] for row in auth_client(trucks_loader).get("/api/loader/history/").data] == [truck.pk]
    assert [row["id"] for row in auth_client(wagons_loader).get("/api/loader/history/").data] == [wagon.pk]


@pytest.mark.parametrize("path", ["queue", "history"])
def test_transport_tab_filters_and_refuses_a_foreign_area(auth_client, user_with_perms, trucks_loader, product, path):
    both = user_with_perms("tabs-loader", codes=["loader.view", "loader.trucks", "loader.wagons"])
    truck = _order(product)
    wagon = _order(product, transport_type="train")
    for order in (truck, wagon):
        if path == "history":
            Order.objects.filter(pk=order.pk).update(status="shipped")
            Shipment.objects.create(order=order, shipped_at=order.created_at)

    api = auth_client(both)
    assert [row["id"] for row in api.get(f"/api/loader/{path}/?transport=truck").data] == [truck.pk]
    assert [row["id"] for row in api.get(f"/api/loader/{path}/?transport=train").data] == [wagon.pk]
    assert api.get(f"/api/loader/{path}/?transport=ship").status_code == 400
    # Вкладка чужой области — отказ, а не пустой список.
    assert auth_client(trucks_loader).get(f"/api/loader/{path}/?transport=train").status_code == 403


def test_foreign_area_order_is_not_found_for_dispatch_rollback_and_waybill(
    auth_client, trucks_loader, wagons_loader, product,
):
    wagon = _order(product, transport_type="train")
    api = auth_client(trucks_loader)

    assert api.post(
        f"/api/loader/orders/{wagon.pk}/dispatch/", {"truck_number": "00123456"}, format="json").status_code == 404
    wagon.refresh_from_db()
    assert wagon.status == "confirmed"

    assert auth_client(wagons_loader).post(
        f"/api/loader/orders/{wagon.pk}/dispatch/", {"truck_number": "00123456"}, format="json").status_code == 200
    assert api.post(f"/api/loader/orders/{wagon.pk}/rollback/", {}, format="json").status_code == 404
    assert api.get(f"/api/loader/orders/{wagon.pk}/waybill/").status_code == 404
    wagon.refresh_from_db()
    assert wagon.status == "shipped"


def test_direct_service_calls_check_the_area(trucks_loader, wagons_loader, user_with_perms, product):
    """Проверка живёт в сервисах: её не обойти другим эндпоинтом, ботом или скриптом."""
    wagon = _order(product, transport_type="train")
    loading_wagon = _order(product, transport_type="train", status="loading")
    loaded_wagon = _order(product, transport_type="train", status="loaded")
    Shipment.objects.create(order=loading_wagon)
    Shipment.objects.create(order=loaded_wagon)
    truck = _order(product)
    arrived_truck = _order(product, status="arrived")
    loading_truck = _order(product, status="loading")
    Shipment.objects.create(order=loading_truck)
    viewer = user_with_perms("area-without-button", codes=["loader.view", "loader.trucks"])

    calls = [
        lambda: services.dispatch_order(wagon, trucks_loader, truck_number="00123456"),
        lambda: services.start_train_loading(wagon, trucks_loader),
        lambda: services.finish_train_loading(loading_wagon, trucks_loader),
        lambda: services.record_count(loading_wagon, 2, trucks_loader),
        lambda: services.record_shipment(loaded_wagon, trucks_loader),
        # Шаги поста фуры — грузчику вагонов тоже закрыты.
        lambda: services.record_arrival(truck, 15000, wagons_loader),
        lambda: services.finish_loading(loading_truck, wagons_loader),
        # Область без кнопки «Отгружено» — тоже отказ.
        lambda: services.dispatch_order(arrived_truck, viewer, truck_number="403BJN13"),
        lambda: services.set_loading_camera(arrived_truck, "cam2", viewer),
    ]
    for call in calls:
        with pytest.raises(PermissionDenied):
            call()

    for order in (wagon, loading_wagon, loaded_wagon, truck, arrived_truck, loading_truck):
        status = order.status
        order.refresh_from_db()
        assert order.status == status
        assert order.loading_camera == ""


def test_system_automation_is_not_a_loader(product):
    """``user=None`` — автоматика камер: у неё нет областей, и её не проверяют."""
    assert_can_ship(None, _order(product, transport_type="train"))


def test_loader_dispatch_checks_the_area_before_closing_the_ai_count(trucks_loader, product, monkeypatch):
    closed = []
    monkeypatch.setattr(counting, "close_session_for_dispatch", lambda order, user: closed.append(order.pk))
    wagon = _order(product, transport_type="train", status="loading")

    with pytest.raises(PermissionDenied):
        services.loader_dispatch(wagon, trucks_loader, truck_number="00123456")

    assert closed == []
    wagon.refresh_from_db()
    assert wagon.status == "loading"


def test_loader_dispatch_closes_the_ai_count_and_ships(trucks_loader, product, monkeypatch):
    closed = []
    monkeypatch.setattr(counting, "close_session_for_dispatch", lambda order, user: closed.append(order.pk))
    truck = _order(product)

    services.loader_dispatch(truck, trucks_loader, truck_number="403 bjn 13", trailer_number="07kg837pb")

    assert closed == [truck.pk]
    truck.refresh_from_db()
    assert (truck.status, truck.truck_number, truck.trailer_number) == ("shipped", "403BJN13", "07KG837PB")


def test_wagon_loading_endpoints_need_the_wagons_area(auth_client, trucks_loader, wagons_loader, product):
    wagon = _order(product, transport_type="train")

    refused = auth_client(trucks_loader).post(f"/api/orders/{wagon.pk}/train/", {"action": "start"}, format="json")
    assert refused.status_code == 403
    wagon.refresh_from_db()
    assert wagon.status == "confirmed"

    allowed = auth_client(wagons_loader).post(f"/api/orders/{wagon.pk}/train/", {"action": "start"}, format="json")
    assert allowed.status_code == 200, allowed.data


def test_rewind_on_the_post_needs_the_area(auth_client, trucks_loader, product):
    wagon = _order(product, transport_type="train", status="loading")
    Shipment.objects.create(order=wagon)

    response = auth_client(trucks_loader).post(f"/api/orders/{wagon.pk}/rewind-loading/", {}, format="json")

    assert response.status_code == 403
    wagon.refresh_from_db()
    assert wagon.status == "loading"


@pytest.fixture
def ai_enabled(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    MonoblockCameraSettings.objects.create(camera_sources=["cam2", "cam3"])


def test_ai_start_stop_and_reset_need_the_area(api_client, trucks_loader, product, ai_enabled):
    wagon = _order(product, transport_type="train")
    api_client.force_authenticate(trucks_loader)

    with patch.object(ai, "_request") as request:
        started = api_client.post("/api/cameras/cam2/ai/", {"order_id": wagon.pk}, format="json")
    assert started.status_code == 403
    request.assert_not_called()
    assert not AiCountingSession.objects.exists()

    Order.objects.filter(pk=wagon.pk).update(status="loading", loading_camera="cam2")
    session = AiCountingSession.objects.create(
        order=wagon, camera="cam2", status=AiCountingSession.ACTIVE, automatically_started=True)
    with patch.object(ai, "_request") as request:
        stopped = api_client.delete("/api/cameras/cam2/ai/", {"order_id": wagon.pk}, format="json")
        reset = api_client.post("/api/cameras/cam2/ai/reset/", {"order_id": wagon.pk}, format="json")
    assert (stopped.status_code, reset.status_code) == (403, 403)
    request.assert_not_called()
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE


def test_session_control_follows_the_orders_area(
    user_with_perms, trucks_loader, wagons_loader, product, django_user_model,
):
    wagon = _order(product, transport_type="train", status="loading", loading_camera="cam2")
    with transaction.atomic():
        session = AiCountingSession.objects.create(
            order=wagon, camera="cam2", status=AiCountingSession.ACTIVE, automatically_started=True)
    session = AiCountingSession.objects.select_related("order").get(pk=session.pk)
    admin = user_with_perms("sessions-admin", codes=["loader.confirm", "loader.wagons", "sys_permissions.manage"])
    # Администратор без области: «Стоп» ему не показываем — сервис всё равно откажет.
    foreign_admin = user_with_perms(
        "foreign-sessions-admin", codes=["loader.confirm", "loader.trucks", "sys_permissions.manage"])
    root = django_user_model.objects.create_superuser(username="sessions-root", password="pass12345")

    assert can_control_session(session, wagons_loader) is True
    assert can_control_session(session, trucks_loader) is False
    assert can_control_session(session, admin) is True
    assert can_control_session(session, foreign_admin) is False
    assert can_control_session(session, root) is True
