from decimal import Decimal
from apps.notifications.services import notify
from apps.common.money import money_string as _d

# Заказы в этих статусах не считаются финансовыми (не учитываются в обороте).
NON_FINANCIAL_STATUSES = {"draft", "pending", "rejected", "cancelled"}

def client_history(client) -> dict:
    """Детализация клиента: продажи, погашения и долги — плоские строки для таблиц."""
    from apps.orders.models import Order, Payment

    orders = list(
        Order.objects.filter(client=client)
        .prefetch_related("items__product", "payments")
        .order_by("-created_at")
    )
    financial = [o for o in orders if o.status not in NON_FINANCIAL_STATUSES]
    debt_orders = [o for o in orders if o.is_debt]

    # Служебный метод "debt" деньгами не является — в погашения не входит.
    # Обход через order__client не проходит через LiveOrderManager,
    # поэтому удалённые (корзина) заказы отсекаем явно.
    payments = list(
        Payment.objects
        .filter(order__client=client, order__deleted_at__isnull=True)
        .exclude(method="debt")
        .select_related("recorded_by", "received_by", "confirmed_by")
        .order_by("-paid_at")
    )

    def sale_row(o):
        items = list(o.items.all())
        return {
            "id": o.id,
            "date": o.created_at.isoformat(),
            "status": o.status,
            "payment_status": o.payment_status,
            "settlement_intent": o.settlement_intent,
            "items": [{"label": str(i.product), "qty": i.quantity} for i in items],
            "bags": sum(i.quantity for i in items),
            "amount": _d(o.total_amount),
            "paid": _d(o.paid_total),
        }

    def payment_row(p):
        employee = p.confirmed_by or p.received_by or p.recorded_by
        return {
            "id": p.id,
            "order_id": p.order_id,
            "date": (p.confirmed_at or p.paid_at).isoformat(),
            "employee": employee.username if employee else None,
            "method": p.method,
            "status": p.status,
            "amount": _d(p.amount),
        }

    def debt_row(o):
        return {
            "id": o.id,
            "date": o.created_at.isoformat(),
            "bags": sum(i.quantity for i in o.items.all()),
            "amount": _d(o.total_amount),
            "paid": _d(o.paid_total),
            "remaining": _d(o.remaining_amount),
        }

    return {
        "client": {"id": client.id, "name": client.name,
                   "phone": client.phone, "country": client.country},
        "summary": {
            "revenue": _d(sum((o.total_amount for o in financial), Decimal("0"))),
            "paid": _d(sum((o.paid_total for o in financial), Decimal("0"))),
            "debt": _d(sum((o.remaining_amount for o in debt_orders), Decimal("0"))),
            "orders_count": len(financial),
        },
        "sales": [sale_row(o) for o in orders],
        "payments": [payment_row(p) for p in payments],
        "debts": [debt_row(o) for o in debt_orders],
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
