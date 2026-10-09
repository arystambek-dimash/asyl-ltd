"""Возвраты до приёмки кладовщиком: «Полностью возвращено», мука — из их строк.

До 0050 возврат проводился сразу, без приёмки: статус у таких строк уже
«full» (db_default колонки). Принял их тот, кто создал, тогда же; мука и
мешки возврата — сумма его строк по товару, всё принято; за деньги — строки
платных (не бонусных) позиций.
"""
from collections import defaultdict

from django.db import migrations
from django.db.models import Exists, F, OuterRef

from apps.catalog.models import Product as CatalogProduct


def _plain_label(order_item) -> str:
    """Подпись без цвета, как ``OrderItem.product_plain_label``: у исторических моделей свойств нет."""
    product = order_item.product
    if product is None:
        return order_item.product_label_snapshot
    return CatalogProduct(name=product.name, weight_kg=product.weight_kg).plain_label


def backfill_goods_return_items(apps, schema_editor):
    GoodsReturn = apps.get_model("orders", "GoodsReturn")
    GoodsReturnItem = apps.get_model("orders", "GoodsReturnItem")
    GoodsReturnLine = apps.get_model("orders", "GoodsReturnLine")
    GoodsReturn.objects.filter(status="full", accepted_at__isnull=True).update(
        accepted_by=F("created_by"), accepted_at=F("created_at"),
    )
    lines = (
        GoodsReturnLine.objects.filter(
            ~Exists(GoodsReturnItem.objects.filter(goods_return=OuterRef("goods_return"))),
        )
        .select_related("goods_return", "order_item__product")
        .order_by("goods_return_id", "id")
    )
    items = {}
    bags = defaultdict(int)
    paid = defaultdict(int)
    for line in lines.iterator(chunk_size=2000):
        order_item = line.order_item
        label = _plain_label(order_item)
        key = (line.goods_return_id, order_item.product_id or label)
        bags[key] += line.bags
        if not order_item.is_bonus:
            paid[key] += line.bags
        items.setdefault(key, GoodsReturnItem(
            goods_return_id=line.goods_return_id,
            product_id=order_item.product_id,
            product_label_snapshot=label,
            checked_at=line.goods_return.accepted_at,
        ))
    for key, item in items.items():
        item.bags = item.accepted_bags = bags[key]
        item.paid_bags = paid[key]
    GoodsReturnItem.objects.bulk_create(items.values(), batch_size=1000)


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0051_goods_return_acceptance_db_on_delete"),
    ]

    operations = [
        migrations.RunPython(backfill_goods_return_items, migrations.RunPython.noop),
    ]
