"""Уже перенесённые строки актов — документом 1С, без цвета, фасовки и мешков."""

from django.db import migrations

ACT_PRODUCT_NAME = "Реализация по акту сверки (1С)"


def show_document(apps, schema_editor):
    ClientActDocument = apps.get_model("client_acts", "ClientActDocument")
    OrderItem = apps.get_model("orders", "OrderItem")
    documents = ClientActDocument.objects.filter(kind__in=("sale", "opening"), order__isnull=False)
    for order_id, document in documents.values_list("order_id", "document").iterator():
        OrderItem.objects.filter(order_id=order_id, product__name=ACT_PRODUCT_NAME).update(
            product_label_snapshot=ACT_PRODUCT_NAME, act_document=document,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("client_acts", "0001_initial"),
        ("orders", "0058_orderitem_act_document"),
    ]

    operations = [migrations.RunPython(show_document, migrations.RunPython.noop)]
