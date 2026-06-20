from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from .models import StockItem, StockReceipt


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
    log_event("receipt", f"Приёмка {bags} мешков", user=user,
              payload={"product": product.id, "bags": bags})
    return receipt


@transaction.atomic
def deduct_stock(product, bags):
    item = StockItem.objects.select_for_update().filter(product=product).first()
    if item is None or item.bags < bags:
        available = 0 if item is None else item.bags
        raise ValidationError({
            "detail": f"Недостаточно мешков на складе (есть {available}, нужно {bags})",
            "code": "insufficient_stock",
        })
    item.bags = F("bags") - bags
    item.save()
    item.refresh_from_db()
    return item
