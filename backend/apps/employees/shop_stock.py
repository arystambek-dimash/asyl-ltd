"""Остатки торговой точки, изолированные от складов мельницы.

Все писатели берут EmployeeShop перед ShopStock; отгрузка дополнительно держит
Order до EmployeeShop, затем обычные Product/StockItem мельницы. Продажи не
блокируют Order или Product, поэтому обратного порядка блокировок нет.
"""
from collections import Counter

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import EmployeeShop, ShopReplenishment, ShopReplenishmentItem, ShopStock


def _quantities(items):
    quantities = Counter()
    for item in items:
        product = item["product"] if isinstance(item, dict) else item.product
        quantity = item["quantity"] if isinstance(item, dict) else item.quantity
        if product is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise ValidationError({"items": "Укажите товар и целое неотрицательное количество"})
        quantities[product.pk] += quantity
    return quantities


def _change_stock(shop, changes):
    EmployeeShop.objects.select_for_update().get(pk=shop.pk)
    rows = {}
    for product_id in sorted(changes):
        row, _ = ShopStock.objects.get_or_create(shop=shop, product_id=product_id)
        rows[product_id] = ShopStock.objects.select_for_update().get(pk=row.pk)
    short = [product_id for product_id, row in rows.items() if row.bags + changes[product_id] < 0]
    if short:
        raise ValidationError({
            "detail": "Недостаточно товара в магазине", "code": "shop_stock_insufficient",
            "products": short,
        })
    for product_id, row in rows.items():
        row.bags += changes[product_id]
        row.save(update_fields=["bags"])


@transaction.atomic
def deduct_shop_stock(shop, items):
    """Списать только остатки магазина. Позиции — dict или модель с product/quantity."""
    _change_stock(shop, {product_id: -quantity for product_id, quantity in _quantities(items).items()})


@transaction.atomic
def restore_shop_stock(shop, items):
    """Вернуть только остатки магазина при отмене его продажи."""
    _change_stock(shop, _quantities(items))


@transaction.atomic
def lock_replenishment_shop(order):
    """Заблокировать исходный магазин прихода либо магазин получателя заказа.

    Отключение магазина не отменяет уже заказанное пополнение. При повторной
    отгрузке после отката получатель остаётся тем же, что у исходного прихода.
    Вызывающий держит Order и транзакцию до изменения складов мельницы.
    """
    receipt = ShopReplenishment.objects.filter(order=order).first()
    shops = EmployeeShop.objects.select_for_update()
    if receipt is not None:
        return shops.get(pk=receipt.shop_id)
    return shops.filter(client_id=order.client_id).first()


@transaction.atomic
def receive_shop_replenishment(order, shop, items):
    """Зачислить подтверждённую отгрузку один раз, сохранив точный состав."""
    if shop is None:
        return
    EmployeeShop.objects.select_for_update().get(pk=shop.pk)
    receipt, created = ShopReplenishment.objects.get_or_create(order=order, defaults={"shop": shop})
    if not created and receipt.reversed_at is None:
        return
    quantities = _quantities(items)
    _change_stock(shop, quantities)
    receipt.items.all().delete()
    ShopReplenishmentItem.objects.bulk_create([
        ShopReplenishmentItem(replenishment=receipt, product_id=product_id, quantity=quantity)
        for product_id, quantity in sorted(quantities.items())
    ])
    receipt.received_at = timezone.now()
    receipt.reversed_at = None
    receipt.save(update_fields=["received_at", "reversed_at"])


@transaction.atomic
def reverse_shop_replenishment(order):
    """Откатить только реально зачисленный приход, до восстановления мельницы.

    Старые отгрузки без записи прихода не меняют новый остаток магазина.
    Если товар уже продан, откат отгрузки целиком запрещён.
    """
    receipt = ShopReplenishment.objects.filter(order=order, reversed_at__isnull=True).first()
    if receipt is None:
        return
    deduct_shop_stock(receipt.shop, list(receipt.items.select_related("product")))
    receipt.reversed_at = timezone.now()
    receipt.save(update_fields=["reversed_at"])


@transaction.atomic
def reconcile_shop_replenishment(order, new_items):
    """Правка отгруженного пополнения: применить разницу и обновить снимок прихода.

    Вызывать до изменения обычного склада, под блокировкой Order. Старые заказы
    без записанного прихода сохраняют прежнюю складскую логику.
    """
    receipt = ShopReplenishment.objects.filter(order=order, reversed_at__isnull=True).first()
    if receipt is None:
        return
    EmployeeShop.objects.select_for_update().get(pk=receipt.shop_id)
    before = dict(receipt.items.values_list("product_id", "quantity"))
    after = _quantities(new_items)
    changes = {product_id: after.get(product_id, 0) - before.get(product_id, 0) for product_id in before.keys() | after.keys()}
    _change_stock(receipt.shop, changes)
    receipt.items.all().delete()
    ShopReplenishmentItem.objects.bulk_create([
        ShopReplenishmentItem(replenishment=receipt, product_id=product_id, quantity=quantity)
        for product_id, quantity in sorted(after.items())
    ])
