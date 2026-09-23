from apps.common.text import dictionary_key
from apps.eventlog.services import log_event
from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from .models import Product, ProductAlias


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


def _locked_alias(key: str) -> ProductAlias | None:
    return ProductAlias.objects.select_for_update().select_related("product").filter(code=key).first()


@transaction.atomic
def remember_product_alias(code: str, product: Product, user, *, move: bool = False) -> ProductAlias:
    """Запомнить код товара из отчёта о вагонах: следующий отчёт с ним разберётся сам.

    Код другого живого товара переносится только с ``move`` (явное «Перенести»
    на странице «Товары»): бот списывает склад по словарю, и молча
    перенаправленный код списал бы не тот товар. Код архивного товара
    свободен. Автор записи остаётся прежним, написание — последнее введённое.
    Права проверяет вызывающий: страница «Товары» (catalog.edit) или разбор
    отчёта у грузчика. Каждое изменение остаётся в журнале.
    """
    key = dictionary_key(code, "код товара", ProductAlias._meta.get_field("code").max_length)
    if not product.is_active:
        raise ValidationError({"detail": f"Товар «{product}» в архиве", "code": "product_archived"})
    spelling = " ".join(str(code).split())
    alias = _locked_alias(key)
    if alias is None:
        try:
            with transaction.atomic():
                alias = ProductAlias.objects.create(code=key, spelling=spelling, product=product, created_by=user)
        except IntegrityError:
            # Тот же код только что запомнил коллега — дальше как с уже известным.
            alias = _locked_alias(key)
    if alias.product_id != product.pk and alias.product.is_active and not move:
        raise ValidationError({
            "detail": f"Код «{alias.display_code}» уже у товара «{alias.product}» — перенести его сюда?",
            "code": "alias_taken",
        })
    alias.product, alias.spelling = product, spelling
    alias.save(update_fields=["product", "spelling"])
    log_event("catalog", f"Код товара в отчётах «{alias.display_code}» → «{product}»", user=user,
              payload={"product_id": product.pk, "code": alias.code})
    return alias


@transaction.atomic
def forget_product_alias(alias: ProductAlias, user) -> None:
    """Убрать код товара из словаря: отчёт с ним снова уйдёт на разбор."""
    log_event("catalog", f"Код товара в отчётах «{alias.display_code}» убран у «{alias.product}»", user=user,
              payload={"product_id": alias.product_id, "code": alias.code})
    alias.delete()
