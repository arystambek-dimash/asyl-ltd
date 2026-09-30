"""Бэкфилл Shipment.stock_deducted для отгрузок, зафиксированных задним числом (D6).

Фиксация (``orders/fixation.py::_fix_shipped``) склад не списывает. До 0018 это
знало только событие журнала ``shipment`` с ``payload.stock_deducted = False``,
поэтому откат и правка такого заказа двигали склад так, будто его списывали.
Флаг ставится старым фиксациям:

* только отгруженные заказы, у которых ``Shipment`` без строк источников: строки
  пишет лишь отгрузка со списанием. Корзина и «удалённые навсегда» тоже входят:
  соединение ``order__status`` не применяет менеджер связанной модели, а у
  исторической ``Order`` нет ни ``all_objects``, ни ``LiveOrderManager``;
* последнее событие ``shipment`` заказа берётся по id, а не по ``created_at``:
  фиксация переносит дату события назад;
* заказ, чей состав правили после фиксации (``order_edit`` с ``shipment_correction``
  и большим id), не трогаем: старый код уже сдвинул его склад. Такие заказы
  печатаются для ручной сверки.

Остатки склада миграция не меняет. Обратный ход — noop: флаг False у новых
фиксаций ставит сам код.
"""
from django.db import migrations
from django.db.models import Max, OuterRef, Subquery


def backfill_fixation_stock_deducted(apps, schema_editor):
    Shipment = apps.get_model("shipments", "Shipment")
    EventLog = apps.get_model("eventlog", "EventLog")
    fixations = set(
        EventLog.objects.filter(event_type="shipment", payload__stock_deducted=False)
        .values_list("pk", flat=True)
    )
    if not fixations:
        return
    last_shipment_event = (
        EventLog.objects.filter(event_type="shipment", order_id=OuterRef("order_id"))
        .order_by("-id")
        .values("id")[:1]
    )
    candidates = list(
        Shipment.objects.filter(order__status="shipped", stock_deducted=True, sources__isnull=True)
        .annotate(event_id=Subquery(last_shipment_event))
        .filter(event_id__in=fixations)
        .values_list("pk", "order_id", "event_id")
    )
    last_correction = dict(
        EventLog.objects.filter(
            event_type="order_edit",
            payload__action="shipment_correction",
            order_id__in=[order_id for _, order_id, _ in candidates],
        )
        .order_by()
        .values("order_id")
        .annotate(last=Max("id"))
        .values_list("order_id", "last")
    )
    fixed, review = [], []
    for shipment_id, order_id, event_id in candidates:
        if last_correction.get(order_id, 0) > event_id:
            review.append(order_id)
        else:
            fixed.append(shipment_id)
    Shipment.objects.filter(pk__in=fixed).update(stock_deducted=False)
    if review:
        print(
            "\n  shipments.0019: после фиксации задним числом правили состав — "
            "флаг «склад не списан» не ставим, сверьте склад вручную, заказы: "
            + ", ".join(f"#{order_id}" for order_id in sorted(review))
        )


class Migration(migrations.Migration):

    dependencies = [
        ("eventlog", "0003_eventlog_eventlog_recent_idx_and_more"),
        ("shipments", "0018_shipment_stock_deducted"),
    ]

    operations = [
        migrations.RunPython(backfill_fixation_stock_deducted, migrations.RunPython.noop),
    ]
