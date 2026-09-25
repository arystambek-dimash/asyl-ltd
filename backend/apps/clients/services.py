from decimal import Decimal

from django.utils import timezone

from apps.common.money import (
    money_string as _d,
    primary_currency,
    sum_by_currency,
)
from apps.notifications.services import notify
from apps.orders.debt import (
    DEBT_STATUS,
    debt_orders,
    financial_orders,
    order_remaining,
    payment_counts_as_paid,
)
from apps.orders.labels import payment_method_label, payment_status_label
from apps.orders.statuses import is_financial


def client_history(client) -> dict:
    """Детализация клиента: продажи, погашения и долги — плоские строки для таблиц."""
    from apps.orders.models import Order, Payment
    from apps.orders.services import reopen_confirmed_payment_error

    orders = list(
        Order.objects.filter(client=client)
        .prefetch_related("items__product", "payments")
        .order_by("-created_at")
    )
    financial = financial_orders(orders)
    debts = debt_orders(orders)

    # Служебный способ «в долг» (Payment.NON_MONEY_METHODS) в погашения не входит.
    # Обход через order__client не проходит через LiveOrderManager,
    # поэтому удалённые (корзина) заказы отсекаем явно.
    payments = list(
        Payment.objects
        .filter(order__client=client, order__deleted_at__isnull=True)
        .exclude(method__in=Payment.NON_MONEY_METHODS)
        .select_related("order", "recorded_by", "received_by", "confirmed_by", "apipay_invoice")
        .prefetch_related("payment_refunds")
        .order_by("-paid_at")
    )

    def sale_row(o):
        items = list(o.items.all())
        return {
            "id": o.id,
            "date": o.created_at.isoformat(),
            "status": o.status,
            # Входит ли заказ в «Сумму продаж» (заявки, отказы и отмены — нет):
            # итог по списку на странице складывает только такие строки.
            "is_financial": is_financial(o.status),
            "settlement_intent": o.settlement_intent,
            "items": [{"label": i.product_label, "qty": i.quantity} for i in items],
            "bags": o.ordered_bags,
            "amount": _d(o.total_amount),
            "paid": _d(o.paid_total),
            "currency": o.currency,
        }

    def payment_row(p):
        employee = p.author
        return {
            "id": p.id,
            "order_id": p.order_id,
            "date": p.recognized_at.isoformat(),
            "employee": employee.username if employee else None,
            "method": p.method,
            "method_label": payment_method_label(p.method),
            "status": p.status,
            "status_label": payment_status_label(p.status),
            "amount": _d(p.amount),
            # Сколько платёж даёт в «Оплачено»: нетто подтверждённой оплаты
            # финансового заказа, иначе ноль. Итог по списку — сумма этих полей.
            "counted_amount": _d(p.net_amount if payment_counts_as_paid(p) else Decimal("0")),
            "currency": p.order.currency,
            "can_reopen": reopen_confirmed_payment_error(p) is None,
            "can_reject": p.status in Payment.IN_PROGRESS_STATUSES,
            "provider": hasattr(p, "apipay_invoice"),
            "refunded_amount": _d(p.refunded_amount),
        }

    def debt_row(o):
        return {
            "id": o.id,
            "date": o.created_at.isoformat(),
            "bags": o.ordered_bags,
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
    days = store.payment_days or []
    if t == "none" or not isinstance(days, list):
        # Дни не списком (старая запись в обход формы) не прочесть: такой
        # график, как и неизвестный тип, оплату не блокирует и не роняет 500.
        return True
    if t == "monthly":
        return on_date.day in days
    if t == "weekly":
        return on_date.isoweekday() in days
    return True


def is_store_overdue(store, on_date) -> bool:
    """Долг магазина просрочен: график оплат задан и ``on_date`` — день оплаты.

    Без графика окно оплаты открыто всегда (:func:`is_payment_window_open`),
    но просрочкой это не считается.
    """
    return store.payment_schedule_type != "none" and is_payment_window_open(store, on_date)


def detect_overdue(store, on_date) -> int:
    """On a payment day, notify about the store's unpaid shipped orders."""
    if not is_store_overdue(store, on_date):
        return 0
    from apps.orders.models import Order
    # Просрочка — это непогашенный долг. Считаем по тому же правилу, что
    # Order.is_debt: денормализованный payment_status может отстать от факта.
    count = len(debt_orders(
        Order.objects.filter(store=store, status=DEBT_STATUS)
        .prefetch_related("items", "payments")
    ))
    subject = f"Просрочка оплаты по магазину «{store.name}»:"
    # «Проверить просрочки» можно нажимать сколько угодно раз — клиенту хватит
    # одного напоминания по магазину в день.
    if count and not store.client.notifications.filter(
        text__startswith=subject, created_at__date=timezone.localdate(),
    ).exists():
        notify(store.client, f"{subject} {count} заказ(ов)")
    return count
