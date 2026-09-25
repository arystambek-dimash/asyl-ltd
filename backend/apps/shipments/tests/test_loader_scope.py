"""Грузчик: области «Фуры» и «Вагоны» со своими правами.

Страница и кнопка — общие (loader.view / loader.confirm), а какой транспорт
человек видит и отгружает, решает область: loader.trucks или loader.wagons.
"""
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.cameras import ai, counting
from apps.cameras.models import AiCountingSession, MonoblockCameraSettings
from apps.cameras.policies import can_control_session
from apps.orders.models import Order
from apps.shipments import services
from apps.shipments.access import allowed_transports, assert_can_ship
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


def test_areas_decide_the_transport_a_loader_sees(user_with_perms, trucks_loader, wagons_loader, admin_user):
    both = user_with_perms("both-loader", codes=["loader.view", "loader.trucks", "loader.wagons"])

    assert allowed_transports(trucks_loader) == ("truck",)
    assert allowed_transports(wagons_loader) == ("train",)
    assert allowed_transports(both) == ("truck", "train")
    assert allowed_transports(admin_user) == ("truck", "train")
    assert allowed_transports(user_with_perms("page-only", codes=["loader.view"])) == ()


def test_queue_and_history_show_only_the_loaders_area(auth_client, trucks_loader, wagons_loader, product, make_order):
    truck = make_order(product)
    wagon = make_order(product, transport_type="train")

    assert [row["id"] for row in auth_client(trucks_loader).get("/api/loader/queue/").data] == [truck.pk]
    assert [row["id"] for row in auth_client(wagons_loader).get("/api/loader/queue/").data] == [wagon.pk]

    auth_client(trucks_loader).post(
        f"/api/loader/orders/{truck.pk}/dispatch/", {"truck_number": "403BJN13"}, format="json")
    auth_client(wagons_loader).post(
        f"/api/loader/orders/{wagon.pk}/dispatch/", {"truck_number": "00123456"}, format="json")

    assert [row["id"] for row in auth_client(trucks_loader).get("/api/loader/history/").data] == [truck.pk]
    assert [row["id"] for row in auth_client(wagons_loader).get("/api/loader/history/").data] == [wagon.pk]


@pytest.mark.parametrize("path", ["queue", "history"])
def test_transport_tab_filters_and_refuses_a_foreign_area(
    auth_client, user_with_perms, trucks_loader, product, path, make_order,
):
    both = user_with_perms("tabs-loader", codes=["loader.view", "loader.trucks", "loader.wagons"])
    truck = make_order(product)
    wagon = make_order(product, transport_type="train")
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
    auth_client, trucks_loader, wagons_loader, product, make_order,
):
    wagon = make_order(product, transport_type="train")
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


def test_direct_service_calls_check_the_area(trucks_loader, user_with_perms, product, make_order):
    """Проверка живёт в сервисах: её не обойти другим эндпоинтом, ботом или скриптом."""
    wagon = make_order(product, transport_type="train")
    truck = make_order(product)
    viewer = user_with_perms("area-without-button", codes=["loader.view", "loader.trucks"])

    calls = [
        lambda: services.dispatch_order(wagon, trucks_loader, truck_number="00123456"),
        lambda: services.ship_rail_report(wagon, [], trucks_loader, station="", shipped_day=timezone.localdate()),
        # Область без кнопки «Отгружено» — тоже отказ.
        lambda: services.dispatch_order(truck, viewer, truck_number="403BJN13"),
    ]
    for call in calls:
        with pytest.raises(PermissionDenied):
            call()

    for order in (wagon, truck):
        order.refresh_from_db()
        assert order.status == "confirmed"
        assert not Shipment.objects.filter(order=order).exists()


def test_system_automation_is_not_a_loader(product, make_order):
    """``user=None`` — автоматика камер: у неё нет областей, и её не проверяют."""
    assert_can_ship(None, make_order(product, transport_type="train"))


def test_loader_dispatch_checks_the_area_before_closing_the_ai_count(
    trucks_loader, product, dispatch_closed_orders, make_order,
):
    wagon = make_order(product, transport_type="train", status="loading")

    with pytest.raises(PermissionDenied):
        services.loader_dispatch(wagon, trucks_loader, truck_number="00123456")

    assert dispatch_closed_orders == []
    wagon.refresh_from_db()
    assert wagon.status == "loading"


def test_loader_dispatch_closes_the_ai_count_and_ships(trucks_loader, product, dispatch_closed_orders, make_order):
    truck = make_order(product)

    services.loader_dispatch(truck, trucks_loader, truck_number="403 bjn 13", trailer_number="07kg837pb")

    assert dispatch_closed_orders == [truck.pk]
    truck.refresh_from_db()
    assert (truck.status, truck.truck_number, truck.trailer_number) == ("shipped", "403BJN13", "07KG837PB")


@pytest.fixture
def ai_enabled(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    MonoblockCameraSettings.objects.create(camera_sources=["cam2", "cam3"])


def test_ai_start_and_stop_need_the_area(trucks_loader, product, ai_enabled, make_order):
    wagon = make_order(product, transport_type="train")

    with patch.object(ai, "_request") as request, pytest.raises(PermissionDenied):
        counting.start("cam2", wagon, trucks_loader)
    request.assert_not_called()
    assert not AiCountingSession.objects.exists()

    Order.objects.filter(pk=wagon.pk).update(status="loading", loading_camera="cam2")
    session = AiCountingSession.objects.create(
        order=wagon, camera="cam2", status=AiCountingSession.ACTIVE, automatically_started=True)
    with patch.object(ai, "_request") as request, pytest.raises(PermissionDenied):
        counting.stop("cam2", wagon, trucks_loader)
    request.assert_not_called()
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE


def test_session_control_follows_the_orders_area(
    user_with_perms,
    trucks_loader,
    wagons_loader,
    product,
    admin_user,
    make_order,
):
    wagon = make_order(product, transport_type="train", status="loading", loading_camera="cam2")
    with transaction.atomic():
        session = AiCountingSession.objects.create(
            order=wagon, camera="cam2", status=AiCountingSession.ACTIVE, automatically_started=True)
    session = AiCountingSession.objects.select_related("order").get(pk=session.pk)
    admin = user_with_perms("sessions-admin", codes=["loader.confirm", "loader.wagons", "sys_permissions.manage"])
    # Администратор без области: «Стоп» ему не показываем — сервис всё равно откажет.
    foreign_admin = user_with_perms(
        "foreign-sessions-admin", codes=["loader.confirm", "loader.trucks", "sys_permissions.manage"])

    assert can_control_session(session, wagons_loader) is True
    assert can_control_session(session, trucks_loader) is False
    assert can_control_session(session, admin) is True
    assert can_control_session(session, foreign_admin) is False
    assert can_control_session(session, admin_user) is True
