from types import SimpleNamespace

import pytest
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.warehouse.models import StockItem, StockMovement, StockReceipt, Warehouse
from apps.warehouse.serializers import WarehouseSerializer
from apps.warehouse.services import (
    adjust_stock,
    deduct_stock,
    ensure_products_available,
    get_default_warehouse,
    lock_stock_item,
    receive_stock,
    reconcile_shipment_stock,
)

pytestmark = pytest.mark.django_db


def _product():
    return Product.objects.create(
        name="Премиум",
        color="Red",
        weight_kg="50",
        price="100.00",
    )


def test_receive_stock_increments(boss):
    prod = _product()
    receive_stock(prod, 100, boss)
    receive_stock(prod, 50, boss)
    assert StockItem.objects.get(product=prod).bags == 150


def test_legacy_call_uses_default_warehouse_and_audits_it(boss):
    prod = _product()

    receipt = receive_stock(prod, 10, boss)

    warehouse = get_default_warehouse()
    item = StockItem.objects.get(product=prod)
    movement = StockMovement.objects.get(product=prod)
    assert item.warehouse == warehouse
    assert receipt.warehouse == warehouse
    assert movement.warehouse == warehouse


def test_legacy_null_stock_row_is_claimed_by_default_warehouse(boss):
    prod = _product()
    legacy = StockItem.objects.create(product=prod, bags=7, warehouse=None)

    receive_stock(prod, 3, boss)

    legacy.refresh_from_db()
    assert legacy.warehouse == get_default_warehouse()
    assert legacy.bags == 10


def test_explicit_warehouse_is_preserved_in_receipt_and_movement(boss):
    warehouse = Warehouse.objects.create(
        code="north",
        name="Северный склад",
    )
    prod = _product()

    receipt = receive_stock(prod, 15, boss, warehouse=warehouse)

    item = StockItem.objects.get(product=prod)
    movement = StockMovement.objects.get(product=prod)
    assert item.warehouse == warehouse
    assert receipt.warehouse == warehouse
    assert movement.warehouse == warehouse


def test_same_product_can_have_independent_balances_in_two_warehouses(boss):
    prod = _product()
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


def test_availability_is_scoped_to_requested_warehouse(boss):
    prod = _product()
    receive_stock(prod, 10, boss)
    other = Warehouse.objects.create(code="east", name="Восточный склад")

    ensure_products_available([prod])
    with pytest.raises(ValidationError) as exc_info:
        ensure_products_available([prod], warehouse=other)

    assert str(exc_info.value.detail["code"]) == "out_of_stock"

    receive_stock(prod, 2, boss, warehouse=other)
    ensure_products_available([prod], warehouse=other)


def test_deduct_stock_reduces(boss):
    prod = _product()
    receive_stock(prod, 100, boss)
    deduct_stock(prod, 30)
    assert StockItem.objects.get(product=prod).bags == 70


def test_deduct_more_than_available_raises(boss):
    prod = _product()
    receive_stock(prod, 10, boss)
    with pytest.raises(ValidationError):
        deduct_stock(prod, 50)


def test_pinned_inactive_warehouse_allows_historical_stock_operations(boss):
    warehouse = Warehouse.objects.create(code="legacy", name="Закрытый склад")
    prod = _product()
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
        require_active=False,
    )

    assert StockItem.objects.get(product=prod).bags == 15
    assert changes[0]["warehouse"] == warehouse.pk


def test_receipt_endpoint_manager_only(auth_client, operator):
    prod = _product()
    resp = auth_client(operator).post(
        "/api/stock/receive/", {"product": prod.id, "bags": 10}, format="json"
    )
    assert resp.status_code == 403


def test_warehouses_api_permissions_and_crud(auth_client, operator, boss):
    listing = auth_client(operator).get("/api/warehouses/")
    assert listing.status_code == 200
    assert listing.data[0]["code"] == "main"

    denied = auth_client(operator).post(
        "/api/warehouses/",
        {"code": "denied", "name": "Нет доступа"},
        format="json",
    )
    assert denied.status_code == 403

    created = auth_client(boss).post(
        "/api/warehouses/",
        {
            "code": "west",
            "name": "Западный склад",
            "address": "Промзона 2",
        },
        format="json",
    )
    assert created.status_code == 201
    assert created.data["code"] == "west"
    assert created.data["name"] == "Западный склад"
    assert created.data["address"] == "Промзона 2"
    assert created.data["is_active"] is True
    assert created.data["is_default"] is False

    updated = auth_client(boss).patch(
        f"/api/warehouses/{created.data['id']}/",
        {"name": "Запад"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["name"] == "Запад"


def test_default_warehouse_cannot_be_disabled_unset_or_deleted(auth_client, boss):
    warehouse = get_default_warehouse()
    api = auth_client(boss)

    disabled = api.patch(
        f"/api/warehouses/{warehouse.pk}/",
        {"is_active": False},
        format="json",
    )
    unset = api.patch(
        f"/api/warehouses/{warehouse.pk}/",
        {"is_default": False},
        format="json",
    )
    deleted = api.delete(f"/api/warehouses/{warehouse.pk}/")

    assert disabled.status_code == 400
    assert unset.status_code == 400
    assert deleted.status_code == 400


def test_main_anchor_stays_active_and_named_after_default_moves(auth_client, boss):
    main = Warehouse.objects.get(code="main")
    secondary = Warehouse.objects.create(code="future-default", name="Новый основной")
    api = auth_client(boss)

    promoted = api.patch(
        f"/api/warehouses/{secondary.pk}/",
        {"is_default": True},
        format="json",
    )
    renamed = api.patch(
        f"/api/warehouses/{main.pk}/",
        {"code": "old-main"},
        format="json",
    )
    disabled = api.patch(
        f"/api/warehouses/{main.pk}/",
        {"is_active": False},
        format="json",
    )
    deleted = api.delete(f"/api/warehouses/{main.pk}/")
    deleted_default = api.delete(f"/api/warehouses/{secondary.pk}/")

    assert promoted.status_code == 200, promoted.data
    assert renamed.status_code == 400
    assert disabled.status_code == 400
    assert deleted.status_code == 400
    assert deleted_default.status_code == 400


def test_stale_update_cannot_disable_a_newly_promoted_default():
    main = get_default_warehouse()
    secondary = Warehouse.objects.create(code="promotion-race", name="Новый основной")
    stale = Warehouse.objects.get(pk=secondary.pk)
    serializer = WarehouseSerializer(
        stale,
        data={"is_active": False},
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors
    Warehouse.objects.filter(pk=main.pk).update(is_default=False)
    Warehouse.objects.filter(pk=secondary.pk).update(is_default=True)

    with pytest.raises(ValidationError):
        serializer.save()

    secondary.refresh_from_db()
    assert secondary.is_default is True
    assert secondary.is_active is True


def test_secondary_product_assignment_cannot_be_deleted_during_rollout(
    auth_client,
    boss,
):
    secondary = Warehouse.objects.create(code="locked-stock", name="Доп. склад")
    product = _product()
    receive_stock(product, 6, boss, warehouse=secondary)
    item = StockItem.objects.get(product=product)

    response = auth_client(boss).delete(f"/api/stock/{item.pk}/")

    assert response.status_code == 400
    assert response.data["code"] == "warehouse_assignment_locked"
    assert StockItem.objects.filter(pk=item.pk, warehouse=secondary, bags=6).exists()


def test_stock_api_filters_and_writes_exact_warehouse(auth_client, boss):
    main = get_default_warehouse()
    other = Warehouse.objects.create(code="remote", name="Удалённый склад")
    main_product = _product()
    other_product = Product.objects.create(
        name="Экстра",
        color="Blue",
        weight_kg="50",
        price="120.00",
    )
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
    movements = api.get(
        "/api/stock/movements/",
        {"warehouse": other.pk},
    )

    assert listing.status_code == 200
    assert [row["product"] for row in listing.data] == [other_product.pk]
    assert listing.data[0]["warehouse"] == other.pk
    assert listing.data[0]["warehouse_name"] == "Удалённый склад"
    assert adjusted.status_code == 200
    assert adjusted.data["bags"] == 25
    assert adjusted.data["warehouse"] == other.pk
    assert movements.status_code == 200
    assert {row["warehouse"] for row in movements.data} == {other.pk}
