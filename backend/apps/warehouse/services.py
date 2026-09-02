from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.eventlog.services import log_event

from .models import StockItem, StockMovement, StockReceipt, Warehouse

DEFAULT_WAREHOUSE_CODE = "main"


def get_default_warehouse():
    """Return the active business default used for new operations."""
    warehouse = Warehouse.objects.filter(is_default=True).order_by("id").first()
    if warehouse is None:
        raise ValidationError(
            {
                "detail": "Основной склад не настроен",
                "code": "default_warehouse_missing",
            }
        )
    return warehouse


def get_compatibility_warehouse():
    """Return the stable owner of rows written without a warehouse."""
    warehouse = Warehouse.objects.filter(code=DEFAULT_WAREHOUSE_CODE).first()
    if warehouse is None:
        raise ValidationError(
            {
                "detail": "Склад совместимости не настроен",
                "code": "compatibility_warehouse_missing",
            }
        )
    return warehouse


def resolve_warehouse(warehouse=None, *, require_active=True):
    """Normalize a Warehouse instance/id; omitted means the legacy default."""
    if warehouse is None:
        resolved = get_default_warehouse()
    else:
        warehouse_id = getattr(warehouse, "pk", warehouse)
        if isinstance(warehouse_id, bool):
            warehouse_id = None
        try:
            warehouse_id = int(warehouse_id)
        except (TypeError, ValueError):
            warehouse_id = None
        resolved = (
            Warehouse.objects.filter(pk=warehouse_id).first()
            if warehouse_id is not None
            else None
        )
        if resolved is None:
            raise ValidationError(
                {"detail": "Склад не найден", "code": "warehouse_not_found"}
            )
    if require_active and not resolved.is_active:
        raise ValidationError(
            {
                "detail": f"Склад «{resolved.name}» отключён",
                "code": "warehouse_inactive",
            }
        )
    return resolved


def _product_in_other_warehouse(product, current):
    raise ValidationError(
        {
            "detail": (
                f"Товар «{product}» уже закреплён за складом "
                f"«{current.name}». На этапе перехода один товар может "
                "принадлежать только одному складу"
            ),
            "code": "product_in_other_warehouse",
            "warehouse": current.pk,
        }
    )


def _locked_stock_item(product, warehouse, *, create):
    """Return the product's Phase-1 stock row under deterministic locks."""
    # The explicit compatibility UNIQUE constraint protects old images.  The
    # product lock turns a concurrent first assignment into a clear domain
    # decision instead of an IntegrityError race between two warehouses.
    type(product).objects.select_for_update().only("pk").get(pk=product.pk)
    item = (
        StockItem.objects.select_for_update(of=("self",))
        .filter(product=product)
        .first()
    )
    if item is None:
        if not create:
            return None
        return StockItem.objects.create(
            product=product,
            warehouse=warehouse,
            bags=0,
        )

    effective = item.warehouse or get_compatibility_warehouse()
    if effective.pk != warehouse.pk:
        _product_in_other_warehouse(product, effective)
    if item.warehouse_id is None:
        # Rows inserted by the rollback image remain valid because the column
        # is nullable.  Claim them lazily for the deterministic main warehouse.
        item.warehouse = effective
        item.save(update_fields=["warehouse"])
    return item


def lock_stock_item(
    product,
    warehouse=None,
    *,
    create=True,
    require_active=True,
):
    """Lock/claim one stock row inside the caller's atomic transaction.

    This is shared with shipment code so a row inserted by the rollback image
    with ``warehouse=NULL`` is claimed by the main warehouse before use.
    """
    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    return _locked_stock_item(product, warehouse, create=create)


def _apply(item, delta, reason, user, note=""):
    """Записать движение склада. item.bags уже обновлён и refresh'нут."""
    StockMovement.objects.create(
        warehouse=item.warehouse,
        product=item.product,
        delta=delta,
        balance_after=item.bags,
        reason=reason,
        note=note,
        created_by=user,
    )


def ensure_products_available(
    products,
    warehouse=None,
    *,
    require_active=True,
):
    """Заказ принимается только на товар, имеющийся на складе.

    products — итерируемое Product; товар без складской карточки или с
    нулевым/отрицательным остатком считается отсутствующим.
    """
    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    unique_products = {product.pk: product for product in products}
    rows = {
        item.product_id: item
        for item in StockItem.objects.filter(
            product_id__in=unique_products,
        ).only("product_id", "warehouse_id", "bags")
    }
    compatibility_id = get_compatibility_warehouse().pk
    missing = []
    for product_id, product in unique_products.items():
        stock = rows.get(product_id)
        stock_warehouse_id = (
            stock.warehouse_id
            if stock is not None and stock.warehouse_id is not None
            else compatibility_id
        )
        if stock is None or stock_warehouse_id != warehouse.pk or stock.bags <= 0:
            missing.append(str(product))
    if missing:
        raise ValidationError(
            {
                "detail": f"Нет в наличии на складе: {', '.join(missing)}",
                "code": "out_of_stock",
            }
        )


@transaction.atomic
def adjust_stock(
    product,
    delta,
    user,
    note="",
    warehouse=None,
    *,
    require_active=True,
):
    """Ручная корректировка остатка на +delta (может быть отрицательной)."""
    delta = int(delta)
    if delta == 0:
        raise ValidationError(
            {"detail": "Изменение должно быть не равно нулю", "code": "invalid_delta"}
        )
    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    item = _locked_stock_item(product, warehouse, create=True)
    if item.bags + delta < 0:
        raise ValidationError(
            {
                "detail": (
                    "Остаток не может стать отрицательным "
                    f"(есть {item.bags}, изменение {delta})"
                ),
                "code": "insufficient_stock",
            }
        )
    item.bags = F("bags") + delta
    item.save()
    item.refresh_from_db()
    _apply(item, delta, "adjustment", user, note)
    sign = "+" if delta > 0 else ""
    log_event(
        "stock_adjust",
        f"Корректировка склада {sign}{delta} мешков",
        user=user,
        payload={
            "warehouse": warehouse.pk,
            "warehouse_code": warehouse.code,
            "product": product.id,
            "delta": delta,
            "balance": item.bags,
            "note": note,
        },
    )
    return item


@transaction.atomic
def delete_stock_item(item, user):
    """Reject ownership deletion while StockItem is the assignment record."""
    # All stock mutations lock Product before StockItem. Keeping one order
    # avoids a Product/StockItem deadlock with concurrent receipts or writes.
    product = Product.objects.select_for_update().get(pk=item.product_id)
    StockItem.objects.select_for_update(of=("self",)).get(
        pk=item.pk,
        product_id=product.pk,
    )
    raise ValidationError(
        {
            "detail": (
                "Товар закреплён за складом. Чтобы убрать остаток, "
                "проведите корректировку до нуля"
            ),
            "code": "warehouse_assignment_locked",
        }
    )


@transaction.atomic
def receive_stock(
    product,
    bags,
    user,
    note="",
    warehouse=None,
    *,
    require_active=True,
):
    if bags <= 0:
        raise ValidationError(
            {
                "detail": "Количество мешков должно быть больше нуля",
                "code": "invalid_bags",
            }
        )
    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    item = _locked_stock_item(product, warehouse, create=True)
    item.bags = F("bags") + bags
    item.save()
    item.refresh_from_db()
    receipt = StockReceipt.objects.create(
        warehouse=warehouse,
        product=product,
        bags=bags,
        received_by=user,
    )
    _apply(item, bags, "receipt", user, note)
    log_event(
        "receipt",
        f"Приёмка {bags} мешков",
        user=user,
        payload={
            "warehouse": warehouse.pk,
            "warehouse_code": warehouse.code,
            "product": product.id,
            "bags": bags,
            "note": note,
        },
    )
    return receipt


@transaction.atomic
def deduct_stock(
    product,
    bags,
    user=None,
    allow_negative=False,
    warehouse=None,
    *,
    require_active=True,
):
    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    item = _locked_stock_item(product, warehouse, create=allow_negative)
    if item is None:
        if not allow_negative:
            raise ValidationError(
                {
                    "detail": f"Недостаточно мешков на складе (есть 0, нужно {bags})",
                    "code": "insufficient_stock",
                }
            )
        item = _locked_stock_item(product, warehouse, create=True)
    if item.bags < bags and not allow_negative:
        raise ValidationError(
            {
                "detail": (
                    "Недостаточно мешков на складе "
                    f"(есть {item.bags}, нужно {bags})"
                ),
                "code": "insufficient_stock",
            }
        )
    if item.bags < bags and allow_negative:
        log_event(
            "stock_negative",
            f"Списание в минус: {product} — было {item.bags}, списано {bags}",
            user=user,
            payload={
                "warehouse": warehouse.pk,
                "warehouse_code": warehouse.code,
                "product": product.id,
                "had": item.bags,
                "deduct": bags,
            },
        )
    item.bags = F("bags") - bags
    item.save()
    item.refresh_from_db()
    _apply(item, -bags, "shipment", user)
    return item


@transaction.atomic
def reconcile_shipment_stock(
    deltas,
    *,
    order,
    user,
    reason,
    warehouse=None,
    require_active=True,
):
    """Apply net stock deltas caused by correcting a shipped order.

    ``deltas`` maps product ids to ``old shipped qty - new shipped qty``.
    Positive values restore bags, negative values deduct additional bags.  All
    stock rows are locked in one global product order so two corrections with
    overlapping mixed products cannot deadlock by taking A/B and B/A locks.

    A post-shipment correction records a historical fact, just like the
    original shipment, so an additional deduction is allowed to take stock
    negative.  The negative balance is still made prominent in the event log.
    The caller must hold the parent Order lock for the whole transaction.
    """
    normalized = {
        int(product_id): int(delta)
        for product_id, delta in dict(deltas or {}).items()
        if int(delta) != 0
    }
    if not normalized:
        return []

    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    rows = {}
    for product_id in sorted(normalized):
        product = Product.objects.get(pk=product_id)
        item = _locked_stock_item(product, warehouse, create=True)
        rows[product_id] = item

    movement_note = (f"Корректировка отгрузки заказа #{order.pk}: {reason}")[:300]
    changes = []
    for product_id in sorted(normalized):
        delta = normalized[product_id]
        item = rows[product_id]
        before = item.bags
        after = before + delta
        if after < 0 and delta < 0:
            log_event(
                "stock_negative",
                f"Списание в минус при корректировке заказа #{order.pk}: "
                f"{item.product} — было {before}, изменение {delta}",
                user=user,
                order=order,
                payload={
                    "warehouse": warehouse.pk,
                    "warehouse_code": warehouse.code,
                    "product": product_id,
                    "had": before,
                    "delta": delta,
                    "balance": after,
                    "action": "shipment_correction",
                },
            )
        item.bags = F("bags") + delta
        item.save(update_fields=["bags"])
        item.refresh_from_db(fields=["bags"])
        _apply(
            item,
            delta,
            "shipment_correction",
            user,
            movement_note,
        )
        changes.append(
            {
                "warehouse": warehouse.pk,
                "product": product_id,
                "delta": delta,
                "balance_before": before,
                "balance_after": item.bags,
            }
        )
    return changes
