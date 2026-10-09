"""Кто вывел возврат из «Ждёт приёмки»: кладовщик или менеджер.

До этой миграции кладовщик только закрывал возврат (событие
``goods_return_status`` с ``accepted_bags``), отменял до приёмки — менеджер.
Решает последнее событие возврата: закрыт кладовщиком — ``True``. Отмену
менеджера кладовщик «Исправить» не вернёт на приёмку.
"""

from django.db import migrations, models


def mark_storekeeper_closed(apps, schema_editor):
    EventLog = apps.get_model("eventlog", "EventLog")
    GoodsReturn = apps.get_model("orders", "GoodsReturn")
    last = {}
    events = EventLog.objects.filter(event_type="goods_return_status").order_by("id").values_list("payload", flat=True)
    for payload in events.iterator(chunk_size=2000):
        last[payload.get("goods_return_id")] = "accepted_bags" in payload and "action" not in payload
    closed = [pk for pk, by_storekeeper in last.items() if by_storekeeper]
    GoodsReturn.objects.filter(pk__in=closed, status__in=("full", "partial", "cancelled")).update(
        closed_by_storekeeper=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("eventlog", "0003_eventlog_eventlog_recent_idx_and_more"),
        ("orders", "0054_link_goods_return_refunds"),
    ]

    operations = [
        migrations.AddField(
            model_name="goodsreturn",
            name="closed_by_storekeeper",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.RunPython(mark_storekeeper_closed, migrations.RunPython.noop),
    ]
