from decimal import Decimal
from apps.notifications.services import notify

# Заказы в этих статусах не считаются финансовыми (не учитываются в обороте).
NON_FINANCIAL_STATUSES = {"draft", "pending", "rejected", "cancelled"}


def _d(v) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


def client_analytics(client) -> dict:
    """Аналитика по клиенту: KPI, разбивка по статусам, динамика, топ товаров, последние."""
    from apps.orders.models import Order
    from apps.orders.serializers import OrderSerializer

    orders = list(
        Order.objects.filter(client=client)
        .prefetch_related("items__product", "payments")
        .order_by("-created_at")
    )
    financial = [o for o in orders if o.status not in NON_FINANCIAL_STATUSES]

    revenue = sum((o.total_amount for o in financial), Decimal("0"))
    paid = sum((o.paid_total for o in financial), Decimal("0"))
    debt = sum((o.remaining_amount for o in orders if o.is_debt), Decimal("0"))
    rejected = sum(1 for o in orders if o.status == "rejected")
    average = (revenue / len(financial)) if financial else Decimal("0")

    # Разбивка по статусам (все статусы, где есть заказы).
    status_labels = dict(zip(
        ["draft", "pending", "confirmed", "arrived", "loading", "loaded",
         "shipped", "rejected", "cancelled"],
        ["Черновик", "На рассмотрении", "Ожидает въезда", "Ожидает загрузки",
         "Загрузка", "Загружен", "Отгружен", "Отклонён", "Отменён"],
    ))
    by_status_map = {}
    for o in orders:
        row = by_status_map.setdefault(o.status, {"count": 0, "amount": Decimal("0")})
        row["count"] += 1
        row["amount"] += o.total_amount
    by_status = [
        {"status": s, "label": status_labels.get(s, s),
         "count": v["count"], "amount": _d(v["amount"])}
        for s, v in by_status_map.items()
    ]

    # Динамика по месяцам (финансовые заказы), последние 8.
    monthly_map = {}
    for o in financial:
        key = o.created_at.strftime("%Y-%m")
        m = monthly_map.setdefault(key, {"month": key, "revenue": Decimal("0"), "paid": Decimal("0")})
        m["revenue"] += o.total_amount
        m["paid"] += o.paid_total
    monthly = [
        {"month": k, "revenue": _d(v["revenue"]), "paid": _d(v["paid"])}
        for k, v in sorted(monthly_map.items())
    ][-8:]

    # Топ товаров по сумме (финансовые заказы), топ-5.
    prod_map = {}
    for o in financial:
        for it in o.items.all():
            row = prod_map.setdefault(it.product_id, {
                "product": it.product_id, "label": str(it.product),
                "qty": 0, "amount": Decimal("0")})
            row["qty"] += it.quantity
            unit = it.unit_price if it.unit_price is not None else it.product.price
            row["amount"] += it.quantity * unit
    top_products = sorted(
        ({**r, "amount": _d(r["amount"])} for r in prod_map.values()),
        key=lambda r: Decimal(r["amount"]), reverse=True,
    )[:5]

    recent = OrderSerializer(orders[:8], many=True).data

    return {
        "client": {"id": client.id, "name": client.name,
                   "phone": client.phone, "country": client.country},
        "kpi": {
            "revenue": _d(revenue), "paid": _d(paid), "debt": _d(debt),
            "average": _d(average), "orders_count": len(financial),
            "rejected_count": rejected,
        },
        "by_status": by_status,
        "monthly": monthly,
        "top_products": top_products,
        "recent_orders": recent,
    }


def is_payment_window_open(store, on_date) -> bool:
    t = store.payment_schedule_type
    if t == "none":
        return True
    if t == "monthly":
        return on_date.day in (store.payment_days or [])
    if t == "weekly":
        return on_date.isoweekday() in (store.payment_days or [])
    return True


def detect_overdue(store, on_date) -> int:
    """On a payment day, notify about the store's unpaid shipped orders."""
    if not is_payment_window_open(store, on_date) or store.payment_schedule_type == "none":
        return 0
    from apps.orders.models import Order
    overdue = Order.objects.filter(
        store=store, status="shipped"
    ).exclude(payment_status="settled")
    count = overdue.count()
    if count:
        notify(store.client,
               f"Просрочка оплаты по магазину «{store.name}»: {count} заказ(ов)")
    return count
