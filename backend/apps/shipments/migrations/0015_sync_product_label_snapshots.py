"""Новое название товара в старых заказах и вагонах отгрузки.

Снимок подписи раньше писался один раз, и переименованный товар в оформленных
заказах оставался под прежним именем. Теперь название переносит
``Product.save``; миграция разово догоняет товары, переименованные до неё:
название в подписи (до первого « · ») — как у товара сейчас, цвет и фасовка —
как при заказе. UPDATE по индексу товара, строки с верным названием не
трогаются. Вес и класс камеры в снимке не меняются.
"""
from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Concat, StrIndex, Substr

SEPARATOR = " · "


def sync_product_label_snapshots(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    snapshots = (apps.get_model("orders", "OrderItem"), apps.get_model("shipments", "ShipmentWagon"))
    for product in Product.objects.only("name").iterator():
        prefix = f"{product.name}{SEPARATOR}"
        rest = Substr("product_label_snapshot", StrIndex("product_label_snapshot", Value(SEPARATOR)) + len(SEPARATOR))
        for model in snapshots:
            model.objects.filter(product_id=product.pk, product_label_snapshot__contains=SEPARATOR).exclude(
                product_label_snapshot__startswith=prefix,
            ).update(product_label_snapshot=Concat(Value(prefix), rest))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0017_remove_product_ask_truck_weight"),
        ("orders", "0047_order_warehouse_contract"),
        ("shipments", "0014_remove_shipment_truck_number"),
    ]

    operations = [
        migrations.RunPython(sync_product_label_snapshots, migrations.RunPython.noop),
    ]
