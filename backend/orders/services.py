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


@transaction.atomic
def confirm_order(order: Order, user) -> Order:
    if order.status != "draft":
        raise ValidationError(
            {"detail": "Подтвердить можно только черновик", "code": "invalid_status"}
        )
    order.status = "confirmed"
    order.save(update_fields=["status"])
    log_event("status", "Заказ подтверждён", user=user, order=order)
    return order
