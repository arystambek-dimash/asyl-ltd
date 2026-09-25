import pytest

from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment
from apps.shipments.services import dispatch_order, rollback_shipment
from apps.warehouse.models import StockItem, StockMovement, Warehouse
from apps.warehouse.services import get_default_warehouse, receive_stock

pytestmark = pytest.mark.django_db


def _client(phone):
    return Client.objects.create_with_user(
        first_name="Склад",
        last_name="Заказ",
        phone=phone,
    )


def _alternate_warehouse(code="north"):
    return Warehouse.objects.create(
        code=code,
        name=f"Склад {code}",
        address="Промзона",
    )


def test_staff_create_defaults_to_main_warehouse_and_serializes_it(
    auth_client,
    manager,
    make_product,
):
    main = get_default_warehouse()
    product = make_product("Основной товар")
    StockItem.objects.create(product=product, warehouse=main, bags=10)
    client = _client("warehouse-create-main")

    response = auth_client(manager).post(
        "/api/orders/",
        {
            "client": client.pk,
            "items": [{"product": product.pk, "quantity": 2}],
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.warehouse_id == main.pk
    assert response.data["warehouse"] == main.pk
    assert response.data["warehouse_name"] == main.name


def test_staff_create_checks_stock_in_explicit_warehouse(auth_client, manager, make_product):
    alternate = _alternate_warehouse()
    product = make_product("Северный товар")
    receive_stock(product, 12, manager, warehouse=alternate)
    client = _client("warehouse-create-north")

    response = auth_client(manager).post(
        "/api/orders/",
        {
            "client": client.pk,
            "warehouse": alternate.pk,
            "items": [{"product": product.pk, "quantity": 3}],
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.warehouse_id == alternate.pk
    assert response.data["warehouse"] == alternate.pk
    assert response.data["warehouse_name"] == alternate.name


def test_portal_creation_pins_default_without_exposing_warehouse(
    auth_client,
    client_user,
    make_product,
):
    main = get_default_warehouse()
    client = Client.objects.create_with_user(
        first_name="Клиент",
        last_name="Портала",
        phone="warehouse-portal",
        user=client_user,
    )
    product = make_product("Портальный товар")
    StockItem.objects.create(product=product, bags=8)

    response = auth_client(client_user).post(
        "/api/portal/orders/",
        {"items": [{"product": product.pk, "quantity": 1}]},
        format="json",
    )

    assert response.status_code == 201, response.data
    order = Order.objects.get(client=client)
    assert order.warehouse_id == main.pk
    assert "warehouse" not in response.data
    assert "warehouse_name" not in response.data


@pytest.mark.parametrize(
    "status",
    ["confirmed", "arrived", "loading", "loaded", "shipped"],
)
def test_warehouse_change_is_locked_after_confirmation(
    auth_client,
    manager,
    status,
):
    main = get_default_warehouse()
    alternate = _alternate_warehouse(code=f"locked-{status}")
    order = Order.objects.create(
        client=_client(f"warehouse-locked-{status}"),
        warehouse=main,
        status=status,
    )

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/",
        {"warehouse": alternate.pk},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "warehouse_locked"
    order.refresh_from_db()
    assert order.warehouse_id == main.pk


def test_pending_empty_order_can_move_before_warehouse_products_are_added(
    auth_client,
    manager,
):
    main = get_default_warehouse()
    alternate = _alternate_warehouse()
    order = Order.objects.create(
        client=_client("warehouse-move-pending"),
        warehouse=main,
        status="pending",
    )

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/",
        {"warehouse": alternate.pk},
        format="json",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order.warehouse_id == alternate.pk


def test_shipment_and_rollback_use_pinned_warehouse(boss, make_product):
    main = get_default_warehouse()
    alternate = _alternate_warehouse()
    product = make_product("Отгружаемый товар")
    receive_stock(product, 20, boss, warehouse=alternate)
    order = Order.objects.create(
        client=_client("warehouse-shipment"),
        warehouse=alternate,
        status="loaded",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=7,
        unit_price="100.00",
    )
    Shipment.objects.create(order=order, bags_loaded=7)
    Warehouse.objects.filter(pk=alternate.pk).update(is_active=False)

    dispatch_order(order, boss)

    assert StockItem.objects.get(product=product, warehouse=alternate).bags == 13
    assert not StockItem.objects.filter(product=product, warehouse=main).exists()

    rollback_shipment(
        order,
        boss,
        target_status="confirmed",
        reason="Исправление склада заказа",
    )

    assert StockItem.objects.get(product=product, warehouse=alternate).bags == 20
    assert set(
        StockMovement.objects.filter(
            product=product,
            reason__in=("shipment", "adjustment"),
        ).values_list("reason", "warehouse_id", "delta")
    ) == {
        ("shipment", alternate.pk, -7),
        ("adjustment", alternate.pk, 7),
    }
    assert not StockMovement.objects.filter(product=product).exclude(
        warehouse=alternate
    ).exists()


def test_shipped_correction_uses_pinned_warehouse(auth_client, manager, make_product):
    alternate = _alternate_warehouse()
    product = make_product("Корректируемый товар")
    receive_stock(product, 15, manager, warehouse=alternate)
    order = Order.objects.create(
        client=_client("warehouse-correction"),
        warehouse=alternate,
        status="shipped",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=5,
        unit_price="100.00",
    )
    Warehouse.objects.filter(pk=alternate.pk).update(is_active=False)

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/",
        {
            "items": [{"product": product.pk, "quantity": 8}],
            "prices": {str(product.pk): "100.00"},
            "edit_reason": "Уточнено фактическое количество",
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    stock = StockItem.objects.get(product=product, warehouse=alternate)
    assert stock.bags == 12
    movement = StockMovement.objects.get(
        product=product,
        reason="shipment_correction",
    )
    assert movement.warehouse_id == alternate.pk
    assert movement.delta == -3
