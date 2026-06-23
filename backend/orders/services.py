from decimal import Decimal
from django.db import transaction
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from .models import Order, Payment


@transaction.atomic
def add_payment(order: Order, amount, user) -> Payment:
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValidationError(
            {"detail": "Сумма оплаты должна быть больше нуля", "code": "invalid_amount"}
        )
    payment = Payment.objects.create(order=order, amount=amount, recorded_by=user)
    log_event("payment", f"Оплата {amount}", user=user, order=order,
              payload={"amount": str(amount)})
    order.refresh_from_db()
    if order.status == "confirmed" and order.is_fully_paid:
        order.status = "paid"
        order.save(update_fields=["status"])
        log_event("status", "Заказ оплачен", user=user, order=order)
    return payment


ALLOWED_TRANSITIONS = {
    "draft": {"pending", "confirmed", "cancelled"},
    "pending": {"confirmed", "rejected", "cancelled"},
    "confirmed": {"paid", "cancelled"},
    "paid": {"arrived", "cancelled"},
    "arrived": {"loading", "cancelled"},
    "loading": {"loaded", "cancelled"},
    "loaded": {"shipped", "cancelled"},
}


@transaction.atomic
def transition(order: Order, to_status: str, user, message: str | None = None) -> Order:
    allowed = ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise ValidationError(
            {"detail": f"Недопустимый переход: {order.status} → {to_status}",
             "code": "invalid_transition"})
    old = order.status
    order.status = to_status
    order.save(update_fields=["status"])
    log_event("status", message or f"Статус: {old} → {to_status}",
              user=user, order=order, payload={"from": old, "to": to_status})
    return order


@transaction.atomic
def confirm_order(order: Order, user) -> Order:
    if order.status not in ("draft", "pending"):
        raise ValidationError(
            {"detail": "Подтвердить можно только новый заказ", "code": "invalid_status"})
    return transition(order, "confirmed", user, "Заказ подтверждён")


@transaction.atomic
def reject_order(order: Order, user) -> Order:
    if order.status != "pending":
        raise ValidationError(
            {"detail": "Отклонить можно только заказ на рассмотрении", "code": "invalid_status"})
    return transition(order, "rejected", user, "Заказ отклонён")
