from apps.notifications.services import notify


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
