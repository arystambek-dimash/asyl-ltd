from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from .models import StockItem, StockReceipt, StockMovement


def _apply(item, delta, reason, user, note=""):
    """Записать движение склада. item.bags уже обновлён и refresh'нут."""
    StockMovement.objects.create(
        product=item.product, delta=delta, balance_after=item.bags,
        reason=reason, note=note, created_by=user,
    )


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
