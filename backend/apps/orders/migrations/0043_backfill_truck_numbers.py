"""Бэкфилл Order.truck_number: номер фуры — слитной записью.

Форма заказа писала номер слитно, грузчик — с пробелами, портал — как ввели,
и одна машина выглядела разными номерами: поиск её не находил, а грузчик
получал ложное «номер задан другим пользователем». Переписываем только номер
фуры и только когда нормализованная запись узнаётся как номер: свободный текст
вроде «САМОВЫВОЗ» остаётся как был. Правила — из единого модуля номеров.
"""
from django.db import migrations

from apps.common.plates import is_known_plate, normalize_plate

BATCH = 1000


def backfill_truck_numbers(apps, schema_editor):
    # У исторической модели нет all_objects: её objects видит и корзину.
    Order = apps.get_model("orders", "Order")
    rows = (
        Order.objects.filter(transport_type="truck")
        .exclude(truck_number="")
        .order_by()
        .only("id", "truck_number")
    )
    fixes = []
    for order in rows.iterator(chunk_size=5000):
        compact = normalize_plate(order.truck_number)
        if compact != order.truck_number and is_known_plate(compact):
            order.truck_number = compact
            fixes.append(order)
    for start in range(0, len(fixes), BATCH):
        Order.objects.bulk_update(fixes[start:start + BATCH], ["truck_number"])


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0042_order_trailer_number"),
    ]

    operations = [
        migrations.RunPython(backfill_truck_numbers, migrations.RunPython.noop),
    ]
