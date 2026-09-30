"""Откат отгрузки по складам-источникам: мешки возвращаются туда, откуда ушли.

Спека docs/superpowers/specs/2026-09-30-loader-warehouse-sources-design.md, §2.1, §2.3, §7.
"""

import pytest
from django.utils import timezone

from apps.eventlog.models import EventLog
from apps.shipments.models import Shipment, ShipmentSource
from apps.shipments.services import rollback_shipment
from apps.warehouse.models import StockItem, StockMovement
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db

MISMATCH = "Склады отгрузки не сходятся с заказом — нужна ручная сверка"


@pytest.fixture
def mills(boss, product, mill, mill_two):
    """«Мельница» (main, склад заказа; у «Д1с» 100 мешков) и «Мельница 2» (50 мешков)."""
    receive_stock(product, 50, boss, warehouse=mill_two)
    return mill, mill_two


def _bags(product, warehouse):
    return StockItem.objects.get(product=product, warehouse=warehouse).bags


def _dispatch(api, order, sources):
    response = api.post(
        f"/api/loader/orders/{order.pk}/dispatch/",
        {"truck_number": "403 BJN 13", "sources": sources},
        format="json",
    )
    assert response.status_code == 200, response.data


def _split(product, main, second):
    """Д1с 20 = Мельница 12 + Мельница 2 8."""
    return [
        {"product": product.pk, "warehouse": main.pk, "bags": 12},
        {"product": product.pk, "warehouse": second.pk, "bags": 8},
    ]


def _restored(product, *cells):
    return [
        {"product": product.pk, "warehouse": warehouse.pk, "warehouse_name": warehouse.name, "bags": bags}
        for warehouse, bags in cells
    ]


def test_loader_undo_returns_split_bags_to_each_warehouse(api_as, trucks_loader, mills, product, make_order):
    main, second = mills
    order = make_order(product, quantity=20)
    api = api_as(trucks_loader)
    _dispatch(api, order, _split(product, main, second))
    assert (_bags(product, main), _bags(product, second)) == (88, 42)

    response = api.post(f"/api/loader/orders/{order.pk}/rollback/", {}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert (_bags(product, main), _bags(product, second)) == (100, 50)
    assert not Shipment.objects.filter(order=order).exists()
    # Строки источников ушли с Shipment каскадом.
    assert not ShipmentSource.objects.filter(product=product).exists()
    event = EventLog.objects.get(order=order, event_type="shipment_rollback")
    assert event.payload["stock_basis"] == "recorded"
    assert event.payload["restored_bags"] == 20
    assert event.payload["restored"] == _restored(product, (main, 12), (second, 8))
    # Проводки списания и возврата остаются в истории — каждая на своём складе.
    assert sorted(
        StockMovement.objects.filter(product=product, reason__in=("shipment", "adjustment"))
        .values_list("reason", "warehouse_id", "delta")
    ) == sorted([
        ("shipment", main.pk, -12),
        ("shipment", second.pk, -8),
        ("adjustment", main.pk, 12),
        ("adjustment", second.pk, 8),
    ])
    assert set(
        StockMovement.objects.filter(product=product, reason="adjustment").values_list("note", flat=True)
    ) == {f"Откат отгрузки заказа #{order.pk}: Ошибочная отгрузка: отмена грузчиком"}


def test_senior_rollback_of_split_then_reship_writes_fresh_rows(
    api_as, trucks_loader, boss, mills, product, make_order
):
    main, second = mills
    order = make_order(product, quantity=20)
    loader_api = api_as(trucks_loader)
    _dispatch(loader_api, order, _split(product, main, second))
    first = Shipment.objects.get(order=order)

    response = api_as(boss).post(
        f"/api/orders/{order.pk}/rollback-shipment/",
        {"status": "confirmed", "reason": "Ошибочно выбран заказ"},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert (_bags(product, main), _bags(product, second)) == (100, 50)
    assert not ShipmentSource.objects.filter(shipment_id=first.pk).exists()
    event = EventLog.objects.get(order=order, event_type="shipment_rollback")
    assert event.payload["restored"] == _restored(product, (main, 12), (second, 8))

    # Повторная отгрузка: новый Shipment и новые строки, старые не воскресают.
    _dispatch(loader_api, order, [{"product": product.pk, "warehouse": second.pk, "bags": 20}])

    again = Shipment.objects.get(order=order)
    assert again.pk != first.pk
    assert list(again.sources.values_list("product_id", "warehouse_id", "bags")) == [(product.pk, second.pk, 20)]
    assert (_bags(product, main), _bags(product, second)) == (100, 30)


@pytest.mark.parametrize("with_shipment", [True, False], ids=["shipment-without-rows", "no-shipment"])
def test_legacy_shipment_rolls_back_onto_order_warehouse_as_before(boss, mills, product, make_order, with_shipment):
    main, second = mills
    order = make_order(product, status="shipped", quantity=7)
    if with_shipment:
        Shipment.objects.create(order=order, bags_loaded=7, shipped_at=timezone.now())

    rollback_shipment(order, boss, target_status="confirmed", reason="Отгрузка до складов-источников")

    assert (_bags(product, main), _bags(product, second)) == (107, 50)
    assert not ShipmentSource.objects.exists()
    event = EventLog.objects.get(order=order, event_type="shipment_rollback")
    assert event.payload["stock_basis"] == "legacy"
    assert event.payload["restored_bags"] == 7
    assert event.payload["restored"] == _restored(product, (main, 7))


def test_rollback_of_undeducted_shipment_returns_nothing(boss, mills, product, make_order):
    main, second = mills
    order = make_order(product, status="shipped", quantity=7)
    Shipment.objects.create(order=order, bags_loaded=7, shipped_at=timezone.now(), stock_deducted=False)

    rollback_shipment(order, boss, target_status="confirmed", reason="Фиксация по ошибке")

    order.refresh_from_db()
    assert order.status == "confirmed"
    assert (_bags(product, main), _bags(product, second)) == (100, 50)
    assert not StockMovement.objects.filter(product=product, reason="adjustment").exists()
    event = EventLog.objects.get(order=order, event_type="shipment_rollback")
    assert event.payload["stock_basis"] == "not_deducted"
    assert event.payload["restored"] == []
    assert event.payload["restored_bags"] == 0


@pytest.mark.parametrize(
    ("rows", "quantity", "healed", "restored"),
    [
        # позиций 23, строк 12 + 8: +3 старого образа легли на склад заказа
        ({"main": 12, "second": 8}, 23, (12, 15), {"main": 15, "second": 8}),
        # всё было с «Мельницы 2», +3 старого образа — новая строка склада заказа
        ({"second": 20}, 23, (0, 3), {"main": 3, "second": 20}),
        # −3 старого образа сняли строку склада заказа до нуля — строка удаляется
        ({"main": 3, "second": 20}, 20, (3, 0), {"second": 20}),
    ],
    ids=["grows-row", "creates-row", "empties-row"],
)
def test_rollback_heals_old_image_drift_onto_order_warehouse(
    boss, mills, product, make_order, rows, quantity, healed, restored
):
    main, second = mills
    warehouses = {"main": main, "second": second}
    order = make_order(product, status="shipped", quantity=quantity)
    shipment = Shipment.objects.create(order=order, bags_loaded=sum(rows.values()), shipped_at=timezone.now())
    for key, bags in rows.items():
        ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=warehouses[key], bags=bags)

    rollback_shipment(order, boss, target_status="confirmed", reason="Сверка после старой правки")

    heal = EventLog.objects.get(order=order, event_type="shipment_sources_healed")
    assert heal.payload["warehouse"] == main.pk
    assert heal.payload["healed"] == [{"product": product.pk, "before": healed[0], "after": healed[1]}]
    event = EventLog.objects.get(order=order, event_type="shipment_rollback")
    assert event.payload["restored"] == _restored(
        product, *((warehouses[key], restored[key]) for key in ("main", "second") if key in restored)
    )
    assert _bags(product, main) == 100 + restored.get("main", 0)
    assert _bags(product, second) == 50 + restored.get("second", 0)


def test_unhealable_drift_refuses_rollback_without_moving_stock(api_as, boss, mills, product, make_order):
    main, second = mills
    order = make_order(product, status="shipped", quantity=5)
    shipment = Shipment.objects.create(order=order, bags_loaded=10, shipped_at=timezone.now())
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=main, bags=2)
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=second, bags=8)

    response = api_as(boss).post(
        f"/api/orders/{order.pk}/rollback-shipment/",
        {"status": "confirmed", "reason": "Ошибочная отгрузка"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "allocation_mismatch"
    assert response.data["detail"] == MISMATCH
    order.refresh_from_db()
    assert order.status == "shipped"
    assert sorted(shipment.sources.values_list("warehouse_id", "bags")) == [(main.pk, 2), (second.pk, 8)]
    assert (_bags(product, main), _bags(product, second)) == (100, 50)
    assert not StockMovement.objects.filter(product=product, reason="adjustment").exists()
    assert not EventLog.objects.filter(
        order=order, event_type__in=("shipment_sources_healed", "shipment_rollback")
    ).exists()
