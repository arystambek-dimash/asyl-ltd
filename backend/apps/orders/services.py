from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from .models import Order, Payment


@transaction.atomic
def add_payment(order: Order, amount, user, method="cash", status="confirmed") -> Payment:
    if order.status != "shipped":
        raise ValidationError(
            {"detail": "Оплата доступна только после отгрузки", "code": "payment_not_open"}
        )
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValidationError(
            {"detail": "Сумма оплаты должна быть больше нуля", "code": "invalid_amount"}
        )
    payment = Payment.objects.create(
        order=order, amount=amount, method=method, status=status, recorded_by=user)
    log_event("payment", f"Оплата {amount} ({method}/{status})", user=user, order=order,
              payload={"amount": str(amount), "method": method, "status": status})
    _apply_payment_status(order, user)
    return payment


@transaction.atomic
def create_client_payment(order: Order, method: str, user) -> Payment:
    # Оплата происходит после въезда машины (статус «arrived»).
    if order.status != "arrived":
        raise ValidationError(
            {"detail": "Оплата доступна после въезда машины", "code": "invalid_status"})
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
    _apply_payment_status(payment.order, user)
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
    order.settlement_intent = "debt"
    order.save(update_fields=["debt_override", "debt_override_by", "settlement_intent"])
    log_event("debt_override", "Долг одобрен", user=user, order=order)
    return order


def _apply_payment_status(order: Order, user) -> None:
    order.refresh_from_db()
    paid = order.paid_total
    if paid <= 0:
        new = "unpaid"
    elif paid >= order.total_amount:
        new = "settled"
    else:
        new = "partial"
    if new != order.payment_status:
        order.payment_status = new
        order.save(update_fields=["payment_status"])
        log_event("payment", f"Статус оплаты: {new}", user=user, order=order,
                  payload={"payment_status": new})


# Логистика: подтверждение → въезд → загрузка → отгрузка. Оплата — отдельно, после shipped.
ALLOWED_TRANSITIONS = {
    "draft": {"pending", "confirmed", "cancelled"},
    "pending": {"confirmed", "rejected", "cancelled"},
    "confirmed": {"arrived", "cancelled"},
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


def can_set_truck_number(order: Order, user) -> bool:
    setter = order.truck_number_set_by
    if setter is None or not order.truck_number:
        return True
    if setter.id == user.id:
        return True
    # number owned by a client → only that client may change it
    if setter.is_client:
        return False
    # number set by staff → any staff may change it
    return not user.is_client


@transaction.atomic
def set_truck_number(order: Order, value: str, user) -> Order:
    if not can_set_truck_number(order, user):
        raise ValidationError(
            {"detail": "Номер КАМАЗа задан другим пользователем", "code": "forbidden"})
    order.truck_number = value
    order.truck_number_set_by = user
    order.save(update_fields=["truck_number", "truck_number_set_by"])
    log_event("status", f"Номер КАМАЗа: {value}", user=user, order=order,
              payload={"truck_number": value})
    return order
