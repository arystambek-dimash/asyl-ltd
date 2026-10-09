import uuid
from collections.abc import Iterable, Mapping

from django.db import transaction
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


def get_main_warehouse(*, lock=False):
    """Return the stable ``main`` warehouse.

    It owns camera stock without an explicit route and is the first row in the
    global warehouse lock order. NO KEY UPDATE still serializes warehouse
    edits/deletes, while remaining compatible with the KEY SHARE lock
    PostgreSQL takes when another stock operation inserts a movement/receipt
    referencing the same warehouse; a full FOR UPDATE here can deadlock with
    that operation's Product lock.
    """
    warehouses = Warehouse.objects.filter(code=DEFAULT_WAREHOUSE_CODE)
    if lock:
        warehouses = warehouses.select_for_update(no_key=True)
    warehouse = warehouses.first()
    if warehouse is None:
        raise ValidationError(
            {
                "detail": "Системный склад не настроен",
                "code": "main_warehouse_missing",
            }
        )
    return warehouse


def resolve_warehouse(warehouse=None, *, require_active=True):
    """Normalize a Warehouse instance/id; omitted means the business default."""
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


def _locked_stock_item(product, warehouse, *, create):
    """Return one warehouse/product row under deterministic locks."""
    # Product is the global mutex for all stock rows of this product.
    type(product).objects.select_for_update().only("pk").get(pk=product.pk)
    item = (
        StockItem.objects.select_for_update(of=("self",))
        .filter(product=product, warehouse=warehouse)
        .select_related("warehouse")
        .first()
    )
    if item is None and create:
        item = StockItem.objects.create(product=product, warehouse=warehouse, bags=0)
    return item


def lock_stock_item(product, warehouse=None, *, require_active=True):
    """Lock one stock row inside the caller's atomic transaction."""
    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    return _locked_stock_item(product, warehouse, create=True)


def lock_stock_items(
    cells: Iterable[tuple[Product, Warehouse]],
) -> dict[tuple[int, int], StockItem]:
    """Lock the stock rows of (product, warehouse) cells in the global order.

    The order is (product_id, warehouse_id): the Product mutex first, then that
    product's warehouses by ascending id. Without a deterministic order, two
    mixed operations taking A/B and B/A would deadlock while each waits for
    the other's row. Repeated cells are locked once. Missing rows are created
    at zero, so allow-negative writers stay deterministic too.
    """
    by_cell = {
        (product.pk, warehouse.pk): (product, warehouse)
        for product, warehouse in cells
    }
    return {
        cell: _locked_stock_item(*by_cell[cell], create=True)
        for cell in sorted(by_cell)
    }


def _post_movement(item, delta, reason, user, note="", *, transfer_id=None):
    """Изменить остаток заблокированной строки и записать движение склада."""
    item.bags += delta
    item.save(update_fields=["bags"])
    StockMovement.objects.create(
        warehouse=item.warehouse,
        product=item.product,
        delta=delta,
        balance_after=item.bags,
        reason=reason,
        note=note,
        created_by=user,
        transfer_id=transfer_id,
    )


def stock_balances(warehouse, product_ids) -> dict[int, int]:
    """Остаток мешков на складе по товарам: {product_id: мешков}.

    Товар без складской карточки — 0.
    """
    product_ids = set(product_ids)
    balances = dict.fromkeys(product_ids, 0)
    rows = StockItem.objects.filter(
        warehouse=warehouse, product_id__in=product_ids
    ).values_list("product_id", "bags")
    balances.update(rows)
    return balances


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
    balances = stock_balances(warehouse, unique_products)
    missing = [
        str(product)
        for product_id, product in unique_products.items()
        if balances[product_id] <= 0
    ]
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
    # Shipments may intentionally overdraw stock. A receipt/rollback must be
    # able to reduce that shortage even when it cannot cover it completely.
    if delta < 0 and item.bags + delta < 0:
        raise ValidationError(
            {
                "detail": (
                    "Остаток не может стать отрицательным "
                    f"(есть {item.bags}, изменение {delta})"
                ),
                "code": "insufficient_stock",
            }
        )
    _post_movement(item, delta, "adjustment", user, note)
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
    receipt = StockReceipt.objects.create(
        warehouse=warehouse,
        product=product,
        bags=bags,
        received_by=user,
    )
    _post_movement(item, bags, "receipt", user, note)
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
def return_stock(product, bags, user, warehouse, note=""):
    """Мешки, которые вернул клиент, — на склад движением «Возврат от клиента».

    Не «Приёмка» (``StockReceipt`` — выпуск производства): возврат не должен
    искажать производство. ``warehouse`` — уже проверенный склад.
    """
    if bags <= 0:
        raise ValidationError(
            {"detail": "Количество мешков должно быть больше нуля", "code": "invalid_bags"}
        )
    item = _locked_stock_item(product, warehouse, create=True)
    _post_movement(item, bags, "client_return", user, note)
    return item


@transaction.atomic
def transfer_stock(
    product,
    bags,
    user,
    *,
    from_warehouse,
    to_warehouse,
    note="",
):
    """Atomically move part of one product balance between warehouses."""
    if bags <= 0:
        raise ValidationError(
            {
                "detail": "Количество мешков должно быть больше нуля",
                "code": "invalid_bags",
            }
        )

    source_warehouse = resolve_warehouse(from_warehouse)
    destination_warehouse = resolve_warehouse(to_warehouse)
    if source_warehouse.pk == destination_warehouse.pk:
        raise ValidationError(
            {
                "detail": "Выберите разные склады",
                "code": "same_warehouse",
            }
        )

    # Match warehouse configuration's global lock order: stable main anchor,
    # then exact warehouse ids. Re-reading under lock closes a concurrent
    # deactivate/delete race before any balance is changed.
    get_main_warehouse(lock=True)
    locked_warehouses = {
        row.pk: row
        for row in Warehouse.objects.select_for_update(no_key=True)
        .filter(pk__in=[source_warehouse.pk, destination_warehouse.pk])
        .order_by("pk")
    }
    source_warehouse = locked_warehouses.get(source_warehouse.pk)
    destination_warehouse = locked_warehouses.get(destination_warehouse.pk)
    if source_warehouse is None or destination_warehouse is None:
        raise ValidationError(
            {"detail": "Склад не найден", "code": "warehouse_not_found"}
        )
    inactive = next(
        (
            row
            for row in (source_warehouse, destination_warehouse)
            if not row.is_active
        ),
        None,
    )
    if inactive is not None:
        raise ValidationError(
            {
                "detail": f"Склад «{inactive.name}» отключён",
                "code": "warehouse_inactive",
            }
        )

    # _locked_stock_item takes the Product mutex before either StockItem. This
    # prevents two concurrent transfers/receipts from creating or spending the
    # same balance out of order, independent of transfer direction.
    source = _locked_stock_item(product, source_warehouse, create=False)
    if source is None or source.bags < bags:
        available = source.bags if source is not None else 0
        raise ValidationError(
            {
                "detail": (
                    "Недостаточно мешков на складе "
                    f"(есть {available}, нужно {bags})"
                ),
                "code": "insufficient_stock",
                "available": available,
            }
        )
    destination = _locked_stock_item(product, destination_warehouse, create=True)

    transfer_id = uuid.uuid4()
    movement_note = (
        f"Перемещение {source_warehouse.name} → {destination_warehouse.name}"
    )
    if note:
        movement_note = f"{movement_note}: {note.strip()}"
    movement_note = movement_note[:300]
    _post_movement(
        source,
        -bags,
        "transfer_out",
        user,
        movement_note,
        transfer_id=transfer_id,
    )
    _post_movement(
        destination,
        bags,
        "transfer_in",
        user,
        movement_note,
        transfer_id=transfer_id,
    )
    log_event(
        "stock_transfer",
        (
            f"Перемещение {bags} мешков: "
            f"{source_warehouse.name} → {destination_warehouse.name}"
        ),
        user=user,
        payload={
            "transfer_id": str(transfer_id),
            "product": product.pk,
            "bags": bags,
            "from_warehouse": source_warehouse.pk,
            "from_warehouse_code": source_warehouse.code,
            "from_balance": source.bags,
            "to_warehouse": destination_warehouse.pk,
            "to_warehouse_code": destination_warehouse.code,
            "to_balance": destination.bags,
            "note": note,
        },
    )
    return {
        "transfer_id": transfer_id,
        "product": product.pk,
        "bags": bags,
        "source": source,
        "destination": destination,
    }


@transaction.atomic
def deduct_stock(
    product,
    bags,
    user=None,
    warehouse=None,
    *,
    require_active=True,
    note="",
    order=None,
    reason="shipment",
):
    """Списание по факту: остаток может уйти в минус.

    ``reason`` — причина проводки: ``shipment`` — отгрузка, ``client_return_undo`` —
    мешки исправленного «Возврата» уходят со склада, куда пришли (их могли уже
    отгрузить). ``note`` пишется в проводку склада, ``order`` — в событие
    «списание в минус», чтобы минус был виден в журнале заказа.
    """
    warehouse = resolve_warehouse(warehouse, require_active=require_active)
    item = _locked_stock_item(product, warehouse, create=True)
    if item.bags < bags:
        log_event(
            "stock_negative",
            f"Списание в минус: {product} — было {item.bags}, списано {bags}",
            user=user,
            order=order,
            payload={
                "warehouse": warehouse.pk,
                "warehouse_code": warehouse.code,
                "product": product.id,
                "had": item.bags,
                "deduct": bags,
            },
        )
    _post_movement(item, -bags, reason, user, note)
    return item


@transaction.atomic
def reconcile_shipment_stock(
    deltas: Mapping[tuple[int, int], int],
    *,
    order,
    user,
    reason,
) -> list[dict]:
    """Apply net stock deltas caused by correcting a shipped order.

    ``deltas`` maps a (product_id, warehouse_id) cell to ``old shipped qty -
    new shipped qty`` of that cell. Positive values restore bags, negative
    values deduct additional bags. All stock rows are locked in one global
    (product, warehouse) order so two corrections with overlapping mixed
    products cannot deadlock by taking A/B and B/A locks.

    A post-shipment correction records a historical fact, just like the
    original shipment, so an additional deduction is allowed to take stock
    negative. The negative balance is still made prominent in the event log.
    The caller must hold the parent Order lock for the whole transaction.
    Like the original shipment, a cell stays pinned to its warehouse even
    after that warehouse is deactivated.
    """
    normalized = {
        (int(product_id), int(warehouse_id)): int(delta)
        for (product_id, warehouse_id), delta in dict(deltas or {}).items()
        if int(delta) != 0
    }
    if not normalized:
        return []

    products = Product.objects.in_bulk({product_id for product_id, _ in normalized})
    warehouses = {
        warehouse_id: resolve_warehouse(warehouse_id, require_active=False)
        for warehouse_id in sorted({warehouse_id for _, warehouse_id in normalized})
    }
    rows = lock_stock_items(
        (products[product_id], warehouses[warehouse_id])
        for product_id, warehouse_id in normalized
    )

    movement_note = (f"Корректировка отгрузки заказа #{order.pk}: {reason}")[:300]
    changes = []
    for cell in sorted(normalized):
        product_id, warehouse_id = cell
        delta = normalized[cell]
        item = rows[cell]
        warehouse = warehouses[warehouse_id]
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
        _post_movement(
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
