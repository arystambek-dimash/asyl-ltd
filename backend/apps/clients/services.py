from decimal import Decimal
from apps.notifications.services import notify
from apps.common.money import money_string as _d
from apps.orders.debt import (
    debt_orders, financial_orders, order_remaining, primary_currency,
    sum_by_currency,
)


def client_history(client) -> dict:
    """Детализация клиента: продажи, погашения и долги — плоские строки для таблиц."""
    from apps.orders.models import Order, Payment

    orders = list(
        Order.objects.filter(client=client)
        .prefetch_related("items__product", "payments")
        .order_by("-created_at")
    )
    financial = financial_orders(orders)
    debts = debt_orders(orders)

    # Служебный метод "debt" деньгами не является — в погашения не входит.
    # Обход через order__client не проходит через LiveOrderManager,
    # поэтому удалённые (корзина) заказы отсекаем явно.
    payments = list(
        Payment.objects
        .filter(order__client=client, order__deleted_at__isnull=True)
        .exclude(method="debt")
        .select_related("order", "recorded_by", "received_by", "confirmed_by")
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
            "items": [{"label": i.product_label, "qty": i.quantity} for i in items],
            "bags": sum(i.quantity for i in items),
            "amount": _d(o.total_amount),
            "paid": _d(o.paid_total),
            "currency": o.currency,
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
            "currency": p.order.currency,
        }

    def debt_row(o):
        return {
            "id": o.id,
            "date": o.created_at.isoformat(),
            "bags": sum(i.quantity for i in o.items.all()),
            "amount": _d(o.total_amount),
            "paid": _d(o.paid_total),
            "remaining": _d(order_remaining(o)),
            "currency": o.currency,
        }

    # Разные валюты не складываются: 1000 ₸ и 5 $ — это не «1005».
    revenue = sum_by_currency(financial, lambda o: o.total_amount)
    paid = sum_by_currency(financial, lambda o: o.paid_total)
    debt = sum_by_currency(debts, order_remaining)
    main = primary_currency(debt or revenue, fallback=client.currency)
    by_currency = {
        currency: {
            "revenue": _d(revenue.get(currency, Decimal("0"))),
            "paid": _d(paid.get(currency, Decimal("0"))),
            "debt": _d(debt.get(currency, Decimal("0"))),
        }
        for currency in sorted({*revenue, *paid, *debt} or {main})
    }

    return {
        "client": {"id": client.id, "name": client.name,
                   "phone": client.phone, "country": client.country,
                   "currency": client.currency},
        "summary": {
            # Плоские поля описывают основную валюту клиента; полная
            # раскладка — в by_currency.
            "currency": main,
            "revenue": _d(revenue.get(main, Decimal("0"))),
            "paid": _d(paid.get(main, Decimal("0"))),
            "debt": _d(debt.get(main, Decimal("0"))),
            "orders_count": len(financial),
            "by_currency": by_currency,
        },
        "sales": [sale_row(o) for o in orders],
        "payments": [payment_row(p) for p in payments],
        "debts": [debt_row(o) for o in debts],
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
    from apps.orders.debt import debt_orders
    # Просрочка — это непогашенный долг, а не любой неоплаченный заказ.
    # Считаем по тому же правилу, что Order.is_debt: денормализованный
    # payment_status может отстать от факта, а моментальная оплата
    # («instant») долгом не является и просрочки не образует.
    count = len(debt_orders(
        Order.objects.filter(store=store, status="shipped")
        .prefetch_related("items", "payments")
    ))
    if count:
        notify(store.client,
               f"Просрочка оплаты по магазину «{store.name}»: {count} заказ(ов)")
    return count
