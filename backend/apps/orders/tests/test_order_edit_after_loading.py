"""Safe order-content corrections after the physical loading workflow."""

from decimal import Decimal

import pytest

from apps.cameras.models import AiCountingSession
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment
from apps.shipments.models import Shipment, ShipmentSource
from apps.warehouse.models import StockItem, StockMovement, Warehouse

pytestmark = pytest.mark.django_db

_sequence = [0]


def _product(*, stock=100):
    _sequence[0] += 1
    product = Product.objects.create(
        name=f"Safe edit product {_sequence[0]}",
        color="Red",
        weight_kg="50",
    )
    if stock is not None:
        StockItem.objects.create(product=product, bags=stock)
    return product


def _order(*, status, rows):
    client = Client.objects.create_with_user(
        first_name="Safe",
        last_name="Edit",
        phone=f"safe-edit-{_sequence[0]}",
    )
    order = Order.objects.create(client=client, status=status)
    for product, quantity, price in rows:
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=quantity,
            unit_price=price,
        )
    return order


def _patch_items(api, order, rows, *, reason=None):
    payload = {
        "items": [
            {"product": product.id, "quantity": quantity}
            for product, quantity, _price in rows
        ],
        "prices": {
            str(product.id): price
            for product, _quantity, price in rows
        },
    }
    if reason is not None:
        payload["edit_reason"] = reason
    return api.patch(
        f"/api/orders/{order.id}/",
        payload,
        format="json",
    )


def _mills():
    """«Мельница» (main — склад заказа) и «Мельница 2»."""
    main = Warehouse.objects.get(code="main")
    main.name = "Мельница"
    main.save(update_fields=["name"])
    return main, Warehouse.objects.create(code="mill-2", name="Мельница 2")


def _shipped_with_sources(product, cells):
    """Отгруженный заказ товара с записанными складами-источниками [(склад, мешки)].

    Остатки складов в тестах — уже после отгрузки: проверяется только сдвиг.
    """
    bags = sum(count for _warehouse, count in cells)
    order = _order(status="shipped", rows=[(product, bags, "100.00")])
    shipment = Shipment.objects.create(order=order, bags_loaded=bags)
    for warehouse, count in cells:
        ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=warehouse, bags=count)
    return order, shipment


def _sources_after(product, cells):
    return [
        {"product": product.id, "warehouse": warehouse.pk, "warehouse_name": warehouse.name, "bags": bags}
        for warehouse, bags in cells
    ]


def test_shipped_correction_reconciles_product_net_deltas_and_audits(manager, api_as):
    first = _product(stock=90)
    removed = _product(stock=46)
    added = _product(stock=30)
    order = _order(
        status="shipped",
        rows=[
            (first, 10, "100.00"),
            (removed, 4, "100.00"),
        ],
    )
    Shipment.objects.create(order=order, bags_loaded=14)

    response = _patch_items(
        api_as(manager),
        order,
        [(first, 6, "100.00"), (added, 7, "100.00")],
        reason="Исправлена накладная",
    )

    assert response.status_code == 200, response.data
    assert "edit_reason" not in response.data
    assert StockItem.objects.get(product=first).bags == 94
    assert StockItem.objects.get(product=removed).bags == 50
    assert StockItem.objects.get(product=added).bags == 23
    assert set(
        StockMovement.objects.filter(reason="shipment_correction")
        .values_list("product_id", "delta")
    ) == {
        (first.id, 4),
        (removed.id, 4),
        (added.id, -7),
    }
    event = EventLog.objects.get(order=order, event_type="order_edit")
    assert event.payload["action"] == "shipment_correction"
    assert event.payload["reason"] == "Исправлена накладная"
    assert event.payload["shipment_bags_loaded"] == 14
    # Отгрузка до складов-источников строк не получает — правка идёт по складу заказа, как раньше.
    assert event.payload["sources_after"] is None
    assert not ShipmentSource.objects.filter(shipment__order=order).exists()
    assert {row["product"]: row["delta"] for row in event.payload["stock_changes"]} == {
        first.id: 4,
        removed.id: 4,
        added.id: -7,
    }
    # The physical counter snapshot is evidence, not an editable order total.
    assert Shipment.objects.get(order=order).bags_loaded == 14


def test_shipped_correction_requires_reason_and_is_atomic(manager, api_as):
    product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(product, 10, "100.00")],
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 5, "100.00")],
    )

    assert response.status_code == 400
    assert response.data["code"] == "edit_reason_required"
    assert order.items.get().quantity == 10
    assert StockItem.objects.get(product=product).bags == 90
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()


def test_shipped_correction_allows_negative_stock_and_logs_warning(manager, api_as):
    product = _product(stock=0)
    order = _order(
        status="shipped",
        rows=[(product, 1, "100.00")],
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 6, "100.00")],
        reason="Уточнён реальный объём",
    )

    assert response.status_code == 200, response.data
    assert StockItem.objects.get(product=product).bags == -5
    movement = StockMovement.objects.get(reason="shipment_correction")
    assert movement.delta == -5
    assert movement.balance_after == -5
    warning = EventLog.objects.get(order=order, event_type="stock_negative")
    assert warning.payload["action"] == "shipment_correction"
    assert warning.payload["balance"] == -5


def test_active_payment_exposure_blocks_shipped_edit_atomically(manager, api_as):
    product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(product, 10, "100.00")],
    )
    Payment.objects.create(
        order=order,
        amount="900.00",
        status="received",
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 5, "100.00")],
        reason="Исправлено количество",
    )

    assert response.status_code == 400
    assert response.data["code"] == "active_payments_exceed_total"
    assert order.items.get().quantity == 10
    assert StockItem.objects.get(product=product).bags == 90
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()


def test_confirmed_overpayment_is_preserved_after_shipped_edit(manager, api_as):
    product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(product, 10, "100.00")],
    )
    payment = Payment.objects.create(
        order=order,
        amount="900.00",
        status="confirmed",
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 5, "100.00")],
        reason="Исправлено количество",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    payment.refresh_from_db()
    assert payment.amount == Decimal("900.00")
    assert payment.status == "confirmed"
    assert order.payment_status == "settled"
    assert order.remaining_amount == Decimal("-400.00")
    assert StockItem.objects.get(product=product).bags == 95


def test_open_ai_session_blocks_item_edits(manager, api_as):
    product = _product(stock=100)
    order = _order(
        status="confirmed",
        rows=[(product, 10, "100.00")],
    )
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam-safe-edit",
        status=AiCountingSession.STARTING,
        started_by=manager,
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 5, "100.00")],
    )

    assert response.status_code == 400
    assert response.data["code"] == "ai_session_active"
    assert order.items.get().quantity == 10
    assert AiCountingSession.objects.filter(pk=session.pk).exists()


def test_loaded_order_can_be_corrected_without_rewriting_ai_snapshot(manager, api_as):
    product = _product(stock=100)
    order = _order(
        status="loaded",
        rows=[(product, 2, "100.00")],
    )
    shipment = Shipment.objects.create(order=order, bags_loaded=2)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam-loaded-edit",
        status=AiCountingSession.CLOSED,
        final_total=2,
        started_by=manager,
        closed_by=manager,
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 3, "100.00")],
    )

    assert response.status_code == 200, response.data
    assert order.items.get().quantity == 3
    assert StockItem.objects.get(product=product).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()
    shipment.refresh_from_db()
    session.refresh_from_db()
    assert shipment.bags_loaded == 2
    assert session.final_total == 2


@pytest.mark.parametrize("status", ["rejected", "cancelled"])
def test_closed_unshipped_order_can_be_corrected_without_stock_movement(
    manager,
    status,
    api_as,
):
    product = _product(stock=100)
    order = _order(
        status=status,
        rows=[(product, 2, "100.00")],
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 3, "100.00")],
    )

    assert response.status_code == 200, response.data
    assert order.items.get().quantity == 3
    assert StockItem.objects.get(product=product).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()


def test_loading_order_remains_locked_without_ai_session(manager, api_as):
    product = _product(stock=100)
    order = _order(
        status="loading",
        rows=[(product, 2, "100.00")],
    )

    response = _patch_items(
        api_as(manager),
        order,
        [(product, 3, "100.00")],
    )

    assert response.status_code == 400
    assert response.data["code"] == "items_locked"
    assert order.items.get().quantity == 2


def test_shipped_edit_with_deleted_historical_product_is_blocked(manager, api_as):
    deleted_product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(deleted_product, 10, "100.00")],
    )
    deleted_product.delete()
    replacement = _product(stock=100)

    response = _patch_items(
        api_as(manager),
        order,
        [(replacement, 10, "100.00")],
        reason="Исправлен удалённый товар",
    )

    assert response.status_code == 400
    assert response.data["code"] == "product_deleted"
    historical = order.items.get()
    assert historical.product_id is None
    assert historical.quantity == 10
    assert StockItem.objects.get(product=replacement).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()


@pytest.mark.parametrize(
    ("shipped", "quantity", "after"),
    [
        ({"main": 12, "second": 8}, 23, {"main": 15, "second": 8}),
        ({"main": 12, "second": 8}, 15, {"main": 7, "second": 8}),
        ({"main": 12, "second": 8}, 5, {"second": 5}),
        ({"second": 20}, 23, {"second": 23}),
    ],
    ids=[
        "plus-3-to-order-warehouse",
        "minus-5-from-order-warehouse",
        "minus-15-empties-order-warehouse",
        "all-from-second-plus-3",
    ],
)
def test_shipped_split_correction_follows_order_warehouse_first_rule(manager, api_as, shipped, quantity, after):
    main, second = _mills()
    warehouses = {"main": main, "second": second}
    product = _product(stock=100)
    StockItem.objects.create(product=product, warehouse=second, bags=50)
    order, shipment = _shipped_with_sources(product, [(warehouses[key], bags) for key, bags in shipped.items()])

    response = _patch_items(api_as(manager), order, [(product, quantity, "100.00")], reason="Исправлена накладная")

    assert response.status_code == 200, response.data
    assert dict(shipment.sources.values_list("warehouse_id", "bags")) == {
        warehouses[key].pk: bags for key, bags in after.items()
    }
    # Остаток каждого склада сдвинулся ровно на изменение его строки.
    assert StockItem.objects.get(product=product, warehouse=main).bags == (
        100 + shipped.get("main", 0) - after.get("main", 0)
    )
    assert StockItem.objects.get(product=product, warehouse=second).bags == (
        50 + shipped.get("second", 0) - after.get("second", 0)
    )
    event = EventLog.objects.get(order=order, event_type="order_edit")
    assert event.payload["sources_after"] == _sources_after(
        product, [(warehouses[key], after[key]) for key in ("main", "second") if key in after]
    )


def test_shipped_split_correction_returns_removed_shares_and_ships_new_product_from_order_warehouse(
    manager, api_as
):
    main, second = _mills()
    removed = _product(stock=100)
    StockItem.objects.create(product=removed, warehouse=second, bags=50)
    added = _product(stock=30)
    order, shipment = _shipped_with_sources(removed, [(main, 12), (second, 8)])

    response = _patch_items(api_as(manager), order, [(added, 5, "100.00")], reason="Заменён товар")

    assert response.status_code == 200, response.data
    assert list(shipment.sources.values_list("product_id", "warehouse_id", "bags")) == [(added.id, main.pk, 5)]
    assert StockItem.objects.get(product=removed, warehouse=main).bags == 112
    assert StockItem.objects.get(product=removed, warehouse=second).bags == 58
    assert StockItem.objects.get(product=added, warehouse=main).bags == 25
    assert not StockItem.objects.filter(product=added, warehouse=second).exists()
    event = EventLog.objects.get(order=order, event_type="order_edit")
    assert {(row["product"], row["warehouse"], row["delta"]) for row in event.payload["stock_changes"]} == {
        (removed.id, main.pk, 12),
        (removed.id, second.pk, 8),
        (added.id, main.pk, -5),
    }
    assert event.payload["sources_after"] == _sources_after(added, [(main, 5)])


def test_shipped_split_correction_plus_then_minus_restores_rows_and_stock(manager, api_as):
    main, second = _mills()
    product = _product(stock=100)
    StockItem.objects.create(product=product, warehouse=second, bags=50)
    order, shipment = _shipped_with_sources(product, [(main, 12), (second, 8)])
    api = api_as(manager)

    assert _patch_items(api, order, [(product, 23, "100.00")], reason="Добавили три мешка").status_code == 200
    assert _patch_items(api, order, [(product, 20, "100.00")], reason="Три мешка вернули").status_code == 200

    assert dict(shipment.sources.values_list("warehouse_id", "bags")) == {main.pk: 12, second.pk: 8}
    assert StockItem.objects.get(product=product, warehouse=main).bags == 100
    assert StockItem.objects.get(product=product, warehouse=second).bags == 50


def test_shipped_correction_of_undeducted_shipment_moves_no_stock(manager, api_as):
    product = _product(stock=100)
    order = _order(status="shipped", rows=[(product, 7, "100.00")])
    Shipment.objects.create(order=order, bags_loaded=7, stock_deducted=False)

    response = _patch_items(api_as(manager), order, [(product, 9, "100.00")], reason="Уточнено количество")

    assert response.status_code == 200, response.data
    assert StockItem.objects.get(product=product).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()
    assert not ShipmentSource.objects.exists()
    event = EventLog.objects.get(order=order, event_type="order_edit")
    assert event.payload["stock_changes"] == []
    assert event.payload["sources_after"] is None


def test_shipped_edit_heals_old_image_drift_before_applying_the_rule(manager, api_as):
    main, second = _mills()
    product = _product(stock=100)
    StockItem.objects.create(product=product, warehouse=second, bags=50)
    order = _order(status="shipped", rows=[(product, 23, "100.00")])
    shipment = Shipment.objects.create(order=order, bags_loaded=20)
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=main, bags=12)
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=second, bags=8)

    response = _patch_items(api_as(manager), order, [(product, 25, "100.00")], reason="Уточнено количество")

    assert response.status_code == 200, response.data
    # +3 старого образа уже списаны со склада заказа: выправляется только строка, склад двигают лишь +2.
    assert dict(shipment.sources.values_list("warehouse_id", "bags")) == {main.pk: 17, second.pk: 8}
    assert StockItem.objects.get(product=product, warehouse=main).bags == 98
    assert StockItem.objects.get(product=product, warehouse=second).bags == 50
    assert EventLog.objects.filter(order=order, event_type="shipment_sources_healed").count() == 1


def test_unhealable_source_drift_blocks_shipped_edit_atomically(manager, api_as):
    main, second = _mills()
    product = _product(stock=100)
    StockItem.objects.create(product=product, warehouse=second, bags=50)
    order = _order(status="shipped", rows=[(product, 5, "100.00")])
    shipment = Shipment.objects.create(order=order, bags_loaded=10)
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=main, bags=2)
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=second, bags=8)

    response = _patch_items(api_as(manager), order, [(product, 6, "100.00")], reason="Уточнено количество")

    assert response.status_code == 400
    assert response.data["code"] == "allocation_mismatch"
    assert order.items.get().quantity == 5
    assert sorted(shipment.sources.values_list("warehouse_id", "bags")) == [(main.pk, 2), (second.pk, 8)]
    assert StockItem.objects.get(product=product, warehouse=main).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()
    assert not EventLog.objects.filter(order=order, event_type="shipment_sources_healed").exists()
