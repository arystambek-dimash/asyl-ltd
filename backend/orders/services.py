from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from .models import Order, Payment


@transaction.atomic
def add_payment(order: Order, amount, user, method="cash", status="confirmed") -> Payment:
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValidationError(
            {"detail": "Сумма оплаты должна быть больше нуля", "code": "invalid_amount"}
        )
    payment = Payment.objects.create(
        order=order, amount=amount, method=method, status=status, recorded_by=user)
    log_event("payment", f"Оплата {amount} ({method}/{status})", user=user, order=order,
              payload={"amount": str(amount), "method": method, "status": status})
    _maybe_mark_paid(order, user)
    return payment


@transaction.atomic
def create_client_payment(order: Order, method: str, user) -> Payment:
    if order.status != "confirmed":
        raise ValidationError(
            {"detail": "Оплата доступна только для подтверждённого заказа", "code": "invalid_status"})
    if method not in ("card", "kaspi"):
        raise ValidationError({"detail": "Недопустимый способ оплаты", "code": "bad_method"})
    remaining = order.total_amount - order.paid_total
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    payment = Payment.objects.create(
        order=order, amount=remaining, method=method, status="pending", recorded_by=user)
    log_event("payment", f"Клиент инициировал оплату {remaining} ({method})",
              user=user, order=order, payload={"amount": str(remaining), "method": method})
    return payment


@transaction.atomic
def confirm_payment(payment: Payment, user) -> Payment:
    payment.status = "confirmed"
    payment.confirmed_by = user
    payment.confirmed_at = timezone.now()
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    log_event("payment", f"Оплата подтверждена {payment.amount}", user=user, order=payment.order,
              payload={"payment_id": payment.id, "amount": str(payment.amount)})
    _maybe_mark_paid(payment.order, user)
    return payment


@transaction.atomic
def reject_payment(payment: Payment, user) -> Payment:
    payment.status = "rejected"
    payment.save(update_fields=["status"])
    log_event("payment", f"Оплата отклонена {payment.amount}", user=user, order=payment.order,
              payload={"payment_id": payment.id})
    return payment


@transaction.atomic
def approve_debt(order: Order, user) -> Order:
    order.debt_override = True
    order.debt_override_by = user
    order.save(update_fields=["debt_override", "debt_override_by"])
    log_event("debt_override", "Долг одобрен", user=user, order=order)
    return transition(order, "paid", user, "Заказ готов (в долг)")


def _maybe_mark_paid(order: Order, user) -> None:
    order.refresh_from_db()
    if order.status == "confirmed" and order.is_fully_paid:
        transition(order, "paid", user, "Заказ оплачен")


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
