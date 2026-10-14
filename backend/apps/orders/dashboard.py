"""Сводка «Главной»: очередь погрузки и отгрузки по дням."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import Max
from django.utils import timezone

from apps.common.query_params import filter_date_range
from apps.eventlog.models import EventLog


def loading_queue(orders):
    """Машины и вагоны на территории: приехали или грузятся."""
    return orders.filter(status__in=("arrived", "loading")).order_by("id")


def shipped_by_day(orders, date_from, date_to) -> list[dict]:
    """Отгруженные мешки по дням, всего и отдельно фуры и вагоны, и их вес.

    У откаченного и заново отгруженного заказа несколько снимков отгрузки;
    текущий — последний записанный (max id). Его created_at может стоять задним
    числом (фиксация, перенос отгрузки), поэтому снимок выбирается до дат.
    Вес ``kg`` — расчётный по фасовке из того же снимка (``bag_estimate_kg``).
    """
    latest_ids = (
        EventLog.objects.filter(order__in=orders.filter(status="shipped"), event_type="shipment")
        .values("order_id")
        .annotate(last=Max("id"))
        .values("last")
    )
    events = filter_date_range(
        EventLog.objects.filter(pk__in=latest_ids),
        "created_at",
        date_from,
        date_to,
    ).values("payload", "created_at", "order__transport_type")

    days: dict[str, dict] = defaultdict(
        lambda: {"bags": 0, "orders": 0, "truck_bags": 0, "train_bags": 0, "kg": Decimal(0)}
    )
    for event in events:
        try:
            bags = max(0, int(event["payload"].get("bags_loaded", 0)))
        except (TypeError, ValueError):
            bags = 0
        try:
            kg = max(Decimal(0), Decimal(str(event["payload"].get("bag_estimate_kg") or 0)))
        except (InvalidOperation, ValueError):
            kg = Decimal(0)
        day = days[timezone.localtime(event["created_at"]).date().isoformat()]
        day["bags"] += bags
        day["orders"] += 1
        day["train_bags" if event["order__transport_type"] == "train" else "truck_bags"] += bags
        day["kg"] += kg
    return [
        {"date": date, **values, "kg": str(values["kg"])} for date, values in sorted(days.items())
    ]
