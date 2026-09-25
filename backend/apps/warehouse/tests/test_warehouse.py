from types import SimpleNamespace

import pytest
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.eventlog.models import EventLog
from apps.warehouse.models import StockItem, StockMovement, StockReceipt, Warehouse
from apps.warehouse.services import (
    adjust_stock,
    deduct_stock,
    ensure_products_available,
    get_default_warehouse,
    lock_stock_item,
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
        {prod.pk: 3},
        order=SimpleNamespace(pk=123),
        user=boss,
        reason="откат",
        warehouse=warehouse,
    )

    assert StockItem.objects.get(product=prod).bags == 15
    assert changes[0]["warehouse"] == warehouse.pk


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
