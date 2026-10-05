"""GET /api/loader/orders/{id}/dispatch-sources/ — предпроверка «Подтвердить отгрузку»
у фуры и склады для опросника «С какого склада?». Ничего не пишет."""
import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments import services
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem, StockMovement, Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def main_warehouse(db):
    """«Основной склад» (code main) — склад заказов фикстуры make_order."""
    return Warehouse.objects.get(code="main")


def _url(order):
    return f"/api/loader/orders/{order.pk}/dispatch-sources/"


def test_truck_with_two_active_warehouses_asks_where_from(
    auth_client, trucks_loader, product, main_warehouse, mill_two, make_order,
):
    order = make_order(product, quantity=2)

    response = auth_client(trucks_loader).get(_url(order))

    assert response.status_code == 200, response.data
    assert response.data == {
        "choose": True,
        # Активные склады — в порядке Meta (имя, id).
        "warehouses": [
            {"id": mill_two.pk, "name": "Мельница 2"},
            {"id": main_warehouse.pk, "name": "Основной склад"},
        ],
        "products": [{
            "product": product.pk,
            "label": "Д1с · 50 кг",
            "bags": 2,
            # На «Основном» 100 ≥ 2 — остаток не показываем; у «Мельницы 2» карточки нет — 0.
            "short": {str(mill_two.pk): 0},
        }],
    }


def test_wagon_does_not_ask(auth_client, wagons_loader, product, mill_two, make_order):
    """Вагоны отгружаются без опросника (D4), даже при двух активных складах."""
    order = make_order(product, transport_type="train")

    response = auth_client(wagons_loader).get(_url(order))

    assert response.status_code == 200, response.data
    assert response.data["choose"] is False


def test_single_active_warehouse_does_not_ask(auth_client, trucks_loader, product, main_warehouse, make_order):
    Warehouse.objects.create(code="closed", name="Закрытый склад", is_active=False)
    order = make_order(product)

    response = auth_client(trucks_loader).get(_url(order))

    assert response.status_code == 200, response.data
    assert response.data["choose"] is False
    assert response.data["warehouses"] == [{"id": main_warehouse.pk, "name": "Основной склад"}]


def test_disabled_order_warehouse_is_not_offered(
    auth_client, trucks_loader, product, main_warehouse, mill_two, make_order,
):
    """Выключенный «Склад отгрузки» при двух активных не предлагается — выбор из активных."""
    closed = Warehouse.objects.create(code="closed", name="Закрытый склад", is_active=False)
    StockItem.objects.create(product=product, warehouse=closed, bags=50)
    order = make_order(product, warehouse=closed)

    response = auth_client(trucks_loader).get(_url(order))

    assert response.status_code == 200, response.data
    assert response.data["choose"] is True
    assert [warehouse["id"] for warehouse in response.data["warehouses"]] == [mill_two.pk, main_warehouse.pk]


def test_short_shows_only_warehouses_without_enough_bags(
    auth_client, trucks_loader, product, make_product, main_warehouse, mill_two, make_order,
):
    """Остаток грузчик видит только при нехватке (D2); нет карточки — 0.

    Товары — в порядке первой позиции заказа, позиции одного товара суммируются.
    """
    blue = make_product(name="Б", color="Blue", weight_kg="25")  # ни одной карточки остатка
    StockItem.objects.create(product=product, warehouse=mill_two, bags=120)  # ровно столько, сколько нужно
    order = make_order(blue, quantity=30)
    OrderItem.objects.create(order=order, product=product, quantity=120, unit_price="10000.00")
    OrderItem.objects.create(order=order, product=blue, quantity=10, unit_price="10000.00")

    response = auth_client(trucks_loader).get(_url(order))

    assert response.status_code == 200, response.data
    assert response.data["products"] == [
        {
            "product": blue.pk,
            "label": "Б · 25 кг",
            "bags": 40,
            "short": {str(mill_two.pk): 0, str(main_warehouse.pk): 0},
        },
        {
            "product": product.pk,
            "label": "Д1с · 50 кг",
            "bags": 120,
            # «Мельница 2»: 120 из 120 — хватает, в short её нет.
            "short": {str(main_warehouse.pk): 100},
        },
    ]


@pytest.mark.parametrize("fields", [{"truck_number": "AB1"}, {"trailer_number": "12"}])
def test_plate_typo_is_refused_before_the_sheet(
    auth_client, trucks_loader, product, mill_two, fields, make_order,
):
    """Опечатку в номере грузчик видит у полей номера — лист не открывается, ничего не записано."""
    order = make_order(product)

    response = auth_client(trucks_loader).get(_url(order), fields)

    assert response.status_code == 400
    assert set(response.data["detail"]) == set(fields)
    order.refresh_from_db()
    assert (order.status, order.truck_number, order.trailer_number) == ("confirmed", "", "")
    assert not Shipment.objects.filter(order=order).exists()


@pytest.mark.parametrize("status", ["pending", "shipped"])
def test_only_orders_waiting_for_shipment(auth_client, trucks_loader, product, mill_two, status, make_order):
    order = make_order(product, status=status)

    response = auth_client(trucks_loader).get(_url(order))

    assert response.status_code == 400
    assert response.data["code"] == "invalid_status"


def test_deleted_product_is_refused_before_the_sheet(auth_client, trucks_loader, product, mill_two, make_order):
    order = make_order(product)
    OrderItem.objects.filter(order=order).update(product=None)

    response = auth_client(trucks_loader).get(_url(order))

    assert response.status_code == 400
    assert response.data["code"] == "product_deleted"
    assert "Д1с · Красный 50 кг" in response.data["detail"]


def test_preflight_writes_nothing_and_keeps_the_ai_count(
    auth_client, trucks_loader, product, main_warehouse, mill_two, dispatch_closed_orders, make_order,
):
    """Номер экрана только проверяется: записывает его и закрывает AI-подсчёт сама отгрузка."""
    order = make_order(product, status="loading")
    session = AiCountingSession.objects.create(order=order, camera="cam2", status=AiCountingSession.ACTIVE)
    events, movements = EventLog.objects.count(), StockMovement.objects.count()

    response = auth_client(trucks_loader).get(_url(order), {"truck_number": "403 bjn 13"})

    assert response.status_code == 200, response.data
    assert response.data["choose"] is True
    order.refresh_from_db()
    session.refresh_from_db()
    assert (order.status, order.truck_number) == ("loading", "")
    assert session.status == AiCountingSession.ACTIVE
    assert dispatch_closed_orders == []
    assert not Shipment.objects.filter(order=order).exists()
    assert (EventLog.objects.count(), StockMovement.objects.count()) == (events, movements)
    assert StockItem.objects.get(product=product, warehouse=main_warehouse).bags == 100


def test_foreign_area_department_and_trash_are_not_found(
    auth_client, trucks_loader, user_with_perms, departments, product, mill_two, make_order,
):
    mill, city = departments
    wagon = make_order(product, transport_type="train")
    assert auth_client(trucks_loader).get(_url(wagon)).status_code == 404

    mill_loader = user_with_perms(
        "mill-loader", codes=["loader.view", "loader.confirm", "loader.trucks"], department=mill)
    own = make_order(product)
    foreign = make_order(product)
    Client.objects.filter(pk=own.client_id).update(department=mill)
    Client.objects.filter(pk=foreign.client_id).update(department=city)
    api = auth_client(mill_loader)
    assert api.get(_url(own)).status_code == 200
    assert api.get(_url(foreign)).status_code == 404

    trashed = make_order(product)
    Order.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
    assert auth_client(trucks_loader).get(_url(trashed)).status_code == 404


def test_without_the_dispatch_button_the_sheet_is_forbidden(
    auth_client, user_with_perms, product, mill_two, make_order,
):
    viewer = user_with_perms("loader-viewer", codes=["loader.view", "loader.trucks"])
    order = make_order(product)

    assert auth_client(viewer).get(_url(order)).status_code == 403


def test_preflight_checks_the_area_in_the_service(trucks_loader, product, make_order):
    """Как у отгрузки: область проверяет сервис, а не только queryset вью."""
    wagon = make_order(product, transport_type="train")

    with pytest.raises(PermissionDenied):
        services.loader_dispatch_preflight(wagon, trucks_loader)
