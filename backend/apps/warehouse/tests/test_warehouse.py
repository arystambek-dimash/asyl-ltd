from types import SimpleNamespace

import pytest
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order
from apps.warehouse import services as warehouse_services
from apps.warehouse.models import StockItem, StockMovement, StockReceipt, Warehouse
from apps.warehouse.services import (
    adjust_stock,
    deduct_stock,
    ensure_products_available,
    get_default_warehouse,
    lock_stock_item,
    lock_stock_items,
    receive_stock,
    reconcile_shipment_stock,
    stock_balances,
)

pytestmark = pytest.mark.django_db


def test_receive_stock_increments(boss, make_product):
    prod = make_product()
    receive_stock(prod, 100, boss)
    receive_stock(prod, 50, boss)
    assert StockItem.objects.get(product=prod).bags == 150


def test_legacy_call_uses_default_warehouse_and_audits_it(boss, make_product):
    prod = make_product()

    receipt = receive_stock(prod, 10, boss)

    warehouse = get_default_warehouse()
    item = StockItem.objects.get(product=prod)
    movement = StockMovement.objects.get(product=prod)
    assert item.warehouse == warehouse
    assert receipt.warehouse == warehouse
    assert movement.warehouse == warehouse


def test_explicit_warehouse_is_preserved_in_receipt_and_movement(boss, make_product):
    warehouse = Warehouse.objects.create(
        code="north",
        name="Северный склад",
    )
    prod = make_product()

    receipt = receive_stock(prod, 15, boss, warehouse=warehouse)

    item = StockItem.objects.get(product=prod)
    movement = StockMovement.objects.get(product=prod)
    assert item.warehouse == warehouse
    assert receipt.warehouse == warehouse
    assert movement.warehouse == warehouse


def test_same_product_can_have_independent_balances_in_two_warehouses(boss, make_product):
    prod = make_product()
    receive_stock(prod, 10, boss)
    other = Warehouse.objects.create(code="south", name="Южный склад")

    receive_stock(prod, 5, boss, warehouse=other)

    main_stock = StockItem.objects.get(
        product=prod,
        warehouse=get_default_warehouse(),
    )
    assert main_stock.bags == 10
    assert StockItem.objects.get(product=prod, warehouse=other).bags == 5
    assert StockReceipt.objects.filter(product=prod).count() == 2


def test_availability_is_scoped_to_requested_warehouse(boss, make_product):
    prod = make_product()
    receive_stock(prod, 10, boss)
    other = Warehouse.objects.create(code="east", name="Восточный склад")

    ensure_products_available([prod])
    with pytest.raises(ValidationError) as exc_info:
        ensure_products_available([prod], warehouse=other)

    assert str(exc_info.value.detail["code"]) == "out_of_stock"

    receive_stock(prod, 2, boss, warehouse=other)
    ensure_products_available([prod], warehouse=other)


def test_stock_balances_are_scoped_to_warehouse(make_product):
    prod = make_product()
    missing = make_product(name="Нет на складе")
    main = Warehouse.objects.get(code="main")
    other = Warehouse.objects.create(code="west", name="Западный склад")
    StockItem.objects.create(product=prod, warehouse=main, bags=5)
    StockItem.objects.create(product=prod, warehouse=other, bags=7)

    assert stock_balances(main, [prod.pk, missing.pk]) == {prod.pk: 5, missing.pk: 0}
    assert stock_balances(other, [prod.pk]) == {prod.pk: 7}
    assert stock_balances(main, []) == {}


def test_deduct_stock_reduces(boss, make_product):
    prod = make_product()
    receive_stock(prod, 100, boss)
    deduct_stock(prod, 30)
    assert StockItem.objects.get(product=prod).bags == 70


def test_deduct_more_than_available_goes_negative_and_logs(boss, make_product):
    prod = make_product()
    receive_stock(prod, 10, boss)
    deduct_stock(prod, 50, boss)
    assert StockItem.objects.get(product=prod).bags == -40
    warning = EventLog.objects.get(event_type="stock_negative")
    assert warning.payload["had"] == 10
    assert warning.payload["deduct"] == 50


def test_pinned_inactive_warehouse_allows_historical_stock_operations(boss, make_product):
    warehouse = Warehouse.objects.create(code="legacy", name="Закрытый склад")
    prod = make_product()
    receive_stock(prod, 10, boss, warehouse=warehouse)
    warehouse.is_active = False
    warehouse.save(update_fields=["is_active"])

    with pytest.raises(ValidationError) as exc_info:
        lock_stock_item(prod, warehouse=warehouse)
    assert str(exc_info.value.detail["code"]) == "warehouse_inactive"

    with pytest.raises(ValidationError):
        ensure_products_available([prod], warehouse=warehouse)
    ensure_products_available(
        [prod],
        warehouse=warehouse,
        require_active=False,
    )

    with transaction.atomic():
        locked = lock_stock_item(
            prod,
            warehouse=warehouse,
            require_active=False,
        )
        assert locked.warehouse == warehouse

    adjust_stock(
        prod,
        2,
        boss,
        warehouse=warehouse,
        require_active=False,
    )
    receive_stock(
        prod,
        1,
        boss,
        warehouse=warehouse,
        require_active=False,
    )
    deduct_stock(
        prod,
        1,
        boss,
        warehouse=warehouse,
        require_active=False,
    )
    changes = reconcile_shipment_stock(
        {(prod.pk, warehouse.pk): 3},
        order=SimpleNamespace(pk=123),
        user=boss,
        reason="откат",
    )

    assert StockItem.objects.get(product=prod).bags == 15
    assert changes[0]["warehouse"] == warehouse.pk


def _shipped_order():
    client = Client.objects.create_with_user(
        first_name="Мурат", phone="+7 (778) 535-22-10", company_name="ИП Мурат"
    )
    return Order.objects.create(client=client, status="shipped")


def test_lock_stock_items_locks_cells_by_product_then_warehouse(monkeypatch, make_product):
    main = Warehouse.objects.get(code="main")
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    first_product = make_product()
    second_product = make_product(name="Эконом", color="Blue")
    locked = []
    original = warehouse_services._locked_stock_item

    def recording(product, warehouse, *, create):
        locked.append((product.pk, warehouse.pk))
        return original(product, warehouse, create=create)

    monkeypatch.setattr(warehouse_services, "_locked_stock_item", recording)

    with transaction.atomic():
        rows = lock_stock_items(
            [
                (second_product, main),
                (first_product, second),
                (second_product, main),
                (first_product, main),
            ]
        )

    expected = sorted(
        [
            (first_product.pk, main.pk),
            (first_product.pk, second.pk),
            (second_product.pk, main.pk),
        ]
    )
    # Мьютекс товара, затем его склады по возрастанию id; дубли ячеек — один раз.
    assert locked == expected
    assert list(rows) == expected
    assert {cell: item.bags for cell, item in rows.items()} == dict.fromkeys(expected, 0)
    assert rows[(first_product.pk, second.pk)].warehouse_id == second.pk


def test_deduct_stock_writes_note_and_links_negative_event_to_order(boss, make_product):
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    prod = make_product()
    order = _shipped_order()
    note = f"Отгрузка заказа #{order.pk}"

    deduct_stock(prod, 8, boss, warehouse=second, note=note, order=order)

    movement = StockMovement.objects.get(product=prod)
    warning = EventLog.objects.get(event_type="stock_negative")
    assert StockItem.objects.get(product=prod, warehouse=second).bags == -8
    assert (movement.warehouse_id, movement.reason, movement.delta, movement.note) == (
        second.pk,
        "shipment",
        -8,
        note,
    )
    assert warning.order_id == order.pk
    assert (warning.payload["warehouse"], warning.payload["had"], warning.payload["deduct"]) == (
        second.pk,
        0,
        8,
    )


def test_reconcile_shipment_stock_applies_each_cell_to_its_warehouse(boss, make_product):
    main = Warehouse.objects.get(code="main")
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    prod = make_product()
    receive_stock(prod, 10, boss, warehouse=main)
    receive_stock(prod, 3, boss, warehouse=second)
    order = _shipped_order()
    note = f"Корректировка отгрузки заказа #{order.pk}: уточнили склады"

    changes = reconcile_shipment_stock(
        {(prod.pk, second.pk): -5, (prod.pk, main.pk): 2},
        order=order,
        user=boss,
        reason="уточнили склады",
    )

    assert StockItem.objects.get(product=prod, warehouse=main).bags == 12
    assert StockItem.objects.get(product=prod, warehouse=second).bags == -2
    assert changes == [
        {"warehouse": main.pk, "product": prod.pk, "delta": 2, "balance_before": 10, "balance_after": 12},
        {"warehouse": second.pk, "product": prod.pk, "delta": -5, "balance_before": 3, "balance_after": -2},
    ]
    assert set(
        StockMovement.objects.filter(reason="shipment_correction").values_list(
            "warehouse_id", "delta", "note"
        )
    ) == {(main.pk, 2, note), (second.pk, -5, note)}
    warning = EventLog.objects.get(event_type="stock_negative")
    assert warning.order_id == order.pk
    assert (warning.payload["warehouse"], warning.payload["delta"], warning.payload["balance"]) == (
        second.pk,
        -5,
        -2,
    )


def test_warehouses_api_permissions_and_crud(auth_client, operator, boss):
    listing = auth_client(operator).get("/api/warehouses/")
    assert listing.status_code == 200
    assert listing.data[0]["code"] == "main"

    denied = auth_client(operator).post(
        "/api/warehouses/",
        {"name": "Нет доступа"},
        format="json",
    )
    assert denied.status_code == 403

    created = auth_client(boss).post(
        "/api/warehouses/",
        {"name": "Западный склад"},
        format="json",
    )
    assert created.status_code == 201
    assert created.data["code"].startswith("wh-")
    assert created.data["name"] == "Западный склад"
    assert created.data["is_active"] is True
    assert created.data["is_default"] is False

    updated = auth_client(boss).patch(
        f"/api/warehouses/{created.data['id']}/",
        {"name": "Запад"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["name"] == "Запад"


def test_warehouse_api_only_renames_and_never_deletes(auth_client, boss):
    main = get_default_warehouse()
    secondary = Warehouse.objects.create(code="west", name="Западный склад")
    api = auth_client(boss)

    patched = api.patch(
        f"/api/warehouses/{secondary.pk}/",
        {
            "code": "renamed",
            "address": "Промзона 2",
            "is_active": False,
            "is_default": True,
        },
        format="json",
    )
    deleted = api.delete(f"/api/warehouses/{secondary.pk}/")

    assert patched.status_code == 200
    assert deleted.status_code == 405
    secondary.refresh_from_db()
    main.refresh_from_db()
    assert (secondary.code, secondary.address) == ("west", "")
    assert secondary.is_active is True
    assert secondary.is_default is False
    assert main.is_default is True


def test_stock_api_filters_and_writes_exact_warehouse(auth_client, boss, make_product):
    main = get_default_warehouse()
    other = Warehouse.objects.create(code="remote", name="Удалённый склад")
    main_product = make_product()
    other_product = make_product(name="Экстра", color="Blue")
    receive_stock(main_product, 11, boss, warehouse=main)
    receive_stock(other_product, 22, boss, warehouse=other)
    api = auth_client(boss)

    listing = api.get("/api/stock/", {"warehouse": other.pk})
    adjusted = api.post(
        "/api/stock/adjust/",
        {
            "warehouse": other.pk,
            "product": other_product.pk,
            "delta": 3,
        },
        format="json",
    )

    assert listing.status_code == 200
    assert [row["product"] for row in listing.data] == [other_product.pk]
    assert listing.data[0]["warehouse"] == other.pk
    assert listing.data[0]["warehouse_name"] == "Удалённый склад"
    assert adjusted.status_code == 200
    assert adjusted.data["bags"] == 25
    assert adjusted.data["warehouse"] == other.pk
    assert set(
        StockMovement.objects.filter(product=other_product).values_list(
            "warehouse_id", flat=True
        )
    ) == {other.pk}
