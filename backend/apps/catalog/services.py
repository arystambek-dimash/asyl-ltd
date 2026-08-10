from apps.eventlog.services import log_event
from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import Product


@transaction.atomic
def archive_product(product: Product, user) -> Product:
    if not product.is_active:
        raise ValidationError({"detail": "Товар уже в архиве", "code": "already_archived"})
    product.is_active = False
    product.save(update_fields=["is_active"])
    log_event("catalog", "Товар отправлен в архив", user=user,
              payload={"product_id": product.id})
    return product


@transaction.atomic
def restore_product(product: Product, user) -> Product:
    if product.is_active:
        raise ValidationError({"detail": "Товар не в архиве", "code": "not_archived"})
    product.is_active = True
    product.save(update_fields=["is_active"])
    log_event("catalog", "Товар восстановлен из архива", user=user,
              payload={"product_id": product.id})
    return product
