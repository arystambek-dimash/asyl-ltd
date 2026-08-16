from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from .models import StockItem, StockMovement, StockReceipt


def _apply(item, delta, reason, user, note=""):
    """Записать движение склада. item.bags уже обновлён и refresh'нут."""
    StockMovement.objects.create(
        product=item.product, delta=delta, balance_after=item.bags,
        reason=reason, note=note, created_by=user,
    )


def ensure_products_available(products):
    """Заказ принимается только на товар, имеющийся на складе.

    products — итерируемое Product; товар без складской карточки или с
    нулевым/отрицательным остатком считается отсутствующим.
    """
    seen = set()
    missing = []
    for p in products:
        if p.id in seen:
            continue
        seen.add(p.id)
        stock = getattr(p, "stock", None)
        if stock is None or stock.bags <= 0:
            missing.append(str(p))
    if missing:
        raise ValidationError({
            "detail": f"Нет в наличии на складе: {', '.join(missing)}",
            "code": "out_of_stock",
        })


@transaction.atomic
def adjust_stock(product, delta, user, note=""):
    """Ручная корректировка остатка на +delta (может быть отрицательной)."""
    delta = int(delta)
    if delta == 0:
        raise ValidationError(
            {"detail": "Изменение должно быть не равно нулю", "code": "invalid_delta"}
        )
    item, _ = StockItem.objects.select_for_update().get_or_create(product=product)
    if item.bags + delta < 0:
        raise ValidationError({
            "detail": f"Остаток не может стать отрицательным (есть {item.bags}, изменение {delta})",
            "code": "insufficient_stock",
        })
    item.bags = F("bags") + delta
    item.save()
    item.refresh_from_db()
    _apply(item, delta, "adjustment", user, note)
    sign = "+" if delta > 0 else ""
    log_event("stock_adjust", f"Корректировка склада {sign}{delta} мешков", user=user,
              payload={"product": product.id, "delta": delta, "balance": item.bags, "note": note})
    return item


@transaction.atomic
def delete_stock_item(item, user):
    """Удалить товар из складского списка, сохранив обнуление в истории."""
    locked = (
        StockItem.objects.select_for_update()
        .select_related("product")
        .get(pk=item.pk)
    )
    product = locked.product
    balance = locked.bags
    if balance:
        StockMovement.objects.create(
            product=product,
            delta=-balance,
            balance_after=0,
            reason="adjustment",
            note="Удаление из складского списка",
            created_by=user,
        )
    locked.delete()
    log_event(
        "stock_remove",
        f"Товар удалён со склада: {product}",
        user=user,
        payload={"product": product.id, "removed_balance": balance},
    )


@transaction.atomic
def receive_stock(product, bags, user):
    if bags <= 0:
        raise ValidationError(
            {"detail": "Количество мешков должно быть больше нуля", "code": "invalid_bags"}
        )
    item, _ = StockItem.objects.select_for_update().get_or_create(product=product)
    item.bags = F("bags") + bags
    item.save()
    item.refresh_from_db()
    receipt = StockReceipt.objects.create(product=product, bags=bags, received_by=user)
    _apply(item, bags, "receipt", user)
    log_event("receipt", f"Приёмка {bags} мешков", user=user,
              payload={"product": product.id, "bags": bags})
    return receipt


@transaction.atomic
def deduct_stock(product, bags, user=None, allow_negative=False):
    item = StockItem.objects.select_for_update().filter(product=product).first()
    if item is None:
        if not allow_negative:
            raise ValidationError({
                "detail": f"Недостаточно мешков на складе (есть 0, нужно {bags})",
                "code": "insufficient_stock",
            })
        item = StockItem.objects.create(product=product, bags=0)
    if item.bags < bags and not allow_negative:
        raise ValidationError({
            "detail": f"Недостаточно мешков на складе (есть {item.bags}, нужно {bags})",
            "code": "insufficient_stock",
        })
    if item.bags < bags and allow_negative:
        log_event("stock_negative",
                  f"Списание в минус: {product} — было {item.bags}, списано {bags}",
                  user=user, payload={"product": product.id, "had": item.bags, "deduct": bags})
    item.bags = F("bags") - bags
    item.save()
    item.refresh_from_db()
    _apply(item, -bags, "shipment", user)
    return item


@transaction.atomic
def reconcile_shipment_stock(deltas, *, order, user, reason):
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

    rows = {}
    for product_id in sorted(normalized):
        item, _ = StockItem.objects.select_for_update().get_or_create(
            product_id=product_id,
        )
        rows[product_id] = item

    movement_note = (
        f"Корректировка отгрузки заказа #{order.pk}: {reason}"
    )[:300]
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
        changes.append({
            "product": product_id,
            "delta": delta,
            "balance_before": before,
            "balance_after": item.bags,
        })
    return changes
