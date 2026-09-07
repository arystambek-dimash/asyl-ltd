"""Shared warehouse-routing rules for camera configuration, reads and posting."""

from __future__ import annotations

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from apps.warehouse.models import StockItem, Warehouse
from apps.warehouse.services import DEFAULT_WAREHOUSE_CODE

from .models import AlwaysOnWarehouseRoute

BASE_COLORS = ("red", "green", "blue")
COLOR_LABELS = {
    "red": "Красный",
    "green": "Зелёный",
    "blue": "Синий",
    "unclassified": "Без цвета",
}


def _compatibility_warehouse(*, lock: bool = False) -> Warehouse:
    warehouses = Warehouse.objects.filter(code=DEFAULT_WAREHOUSE_CODE)
    if lock:
        warehouses = warehouses.select_for_update(no_key=True)
    warehouse = warehouses.first()
    if warehouse is None:
        raise ValidationError(
            {
                "warehouse": "Системный склад не настроен",
                "code": "compatibility_warehouse_not_configured",
            }
        )
    return warehouse


def _warehouse_for_camera(
    camera: str,
    *,
    lock: bool = False,
    require_active: bool = True,
) -> Warehouse:
    routes = AlwaysOnWarehouseRoute.objects.filter(camera=camera)
    routes = routes.select_for_update() if lock else routes.select_related("warehouse")
    route = routes.first()
    warehouse = None
    if route and route.warehouse_id:
        if lock:
            warehouse = (
                Warehouse.objects.select_for_update(no_key=True)
                .filter(pk=route.warehouse_id)
                .first()
            )
        else:
            warehouse = route.warehouse
    # Cameras created by the previous image have no route. Their mappings and
    # stock belong to the stable compatibility warehouse, even if an operator
    # later promotes another warehouse as the default for new orders.
    warehouse = warehouse or _compatibility_warehouse(lock=lock)
    if require_active and not warehouse.is_active:
        raise ValidationError(
            {
                "warehouse": f"Склад «{warehouse.name}» отключён",
                "code": "warehouse_inactive",
            }
        )
    return warehouse


def _effective_stock_warehouse_id(
    stock_item: StockItem,
    *,
    compatibility_warehouse_id: int,
) -> int:
    # A previous image can insert NULL during the expand rollout.  Such a row
    # is legacy main-warehouse stock until the contract migration makes the
    # column mandatory.
    return stock_item.warehouse_id or compatibility_warehouse_id


def _stock_scope_for_warehouse(warehouse: Warehouse) -> Q:
    scope = Q(warehouse=warehouse)
    if warehouse.code == DEFAULT_WAREHOUSE_CODE:
        scope |= Q(warehouse__isnull=True)
    return scope
