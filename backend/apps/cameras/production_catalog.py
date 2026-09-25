"""Shared warehouse-routing rules for camera configuration, reads and posting."""

from __future__ import annotations

from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.warehouse.models import Warehouse
from apps.warehouse.services import get_main_warehouse

from .models import AlwaysOnWarehouseRoute

BASE_COLORS = tuple(value.lower() for value, _label in Product.COLORS)
COLOR_LABELS = {value.lower(): label for value, label in Product.COLORS} | {
    "unclassified": "Без цвета"
}


def color_label(color: str) -> str:
    return COLOR_LABELS.get(color, color)


def _warehouse_for_camera(
    camera: str,
    *,
    lock: bool = False,
    require_active: bool = True,
) -> Warehouse:
    routes = AlwaysOnWarehouseRoute.objects.filter(camera=camera)
    routes = routes.select_for_update() if lock else routes.select_related("warehouse")
    route = routes.first()
    if route is None:
        # A camera without a route uses the stable main warehouse, even if an
        # operator later promotes another warehouse as the default for orders.
        warehouse = get_main_warehouse(lock=lock)
    elif lock:
        warehouse = Warehouse.objects.select_for_update(no_key=True).get(
            pk=route.warehouse_id
        )
    else:
        warehouse = route.warehouse
    if require_active and not warehouse.is_active:
        raise ValidationError(
            {
                "warehouse": f"Склад «{warehouse.name}» отключён",
                "code": "warehouse_inactive",
            }
        )
    return warehouse
