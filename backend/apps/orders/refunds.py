"""Возвраты оплат: проверки, резерв суммы и кассовый возврат.

Общие для всех способов возврата — кассы, счёта ApiPay (``apipay.create_refund``)
и ссылки Kaspi QR (``qr_refunds``). HTTP-клиент провайдера здесь не нужен.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from .models import Order, Payment, PaymentRefund
from .services import lock_live_order, sync_payment_status


def required_refund_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({
            "detail": "Укажите причину возврата.",
            "code": "refund_reason_required",
        })
    return reason


def assert_payment_refundable(payment: Payment) -> None:
    if payment.status != "confirmed":
        raise ValidationError({
            "detail": "Вернуть можно только подтверждённую оплату.",
            "code": "payment_not_confirmed",
        })


def validated_refund_amount(payment: Payment, amount: object) -> Decimal:
    raw = payment.available_for_refund if amount in (None, "") else amount
    try:
        parsed = Decimal(str(raw))
        value = parsed.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValidationError({"detail": "Некорректная сумма возврата."}) from exc
    if not value.is_finite() or value <= 0:
        raise ValidationError({"detail": "Сумма возврата должна быть больше нуля."})
    if parsed != value:
        raise ValidationError({
            "detail": "Укажите сумму возврата с точностью не более двух знаков."
        })
    if value > payment.available_for_refund:
        raise ValidationError({
            "detail": (
                f"Доступно к возврату: "
                f"{payment.available_for_refund} {payment.order.currency}."
            ),
            "code": "refund_exceeds_available",
        })
    return value


def sync_refund_totals(payment: Payment, order: Order) -> None:
    totals = payment.payment_refunds.values("status").annotate(total=Sum("amount"))
    by_status = {row["status"]: row["total"] for row in totals}
    payment.refunded_amount = by_status.get("completed", Decimal(0))
    payment.pending_refund_amount = by_status.get("pending", Decimal(0))
    payment.save(update_fields=["refunded_amount", "pending_refund_amount"])
    sync_payment_status(order)


def settle_reserved_refund(
    refund_id: int, *, status: str, amount: Decimal | None = None
) -> None:
    """Применить исход резерва возврата и пересчитать суммы оплаты.

    Блокировки в каноническом порядке Order -> Payment -> PaymentRefund, как у
    кассы и возвратов ApiPay: обратный порядок даёт дедлок на той же оплате.
    """
    payment_id, order_id = PaymentRefund.objects.values_list(
        "payment_id", "payment__order_id"
    ).get(pk=refund_id)
    with transaction.atomic():
        order = Order.all_objects.select_for_update().get(pk=order_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        refund = PaymentRefund.objects.select_for_update().get(pk=refund_id)
        refund.status = status
        if amount is not None and amount > 0:
            refund.amount = min(amount, payment.amount)
        refund.completed_at = timezone.now() if status == "completed" else None
        refund.save(update_fields=["status", "amount", "completed_at", "updated_at"])
        payment.order = order
        sync_refund_totals(payment, order)


@transaction.atomic
def create_cash_refund(
    payment: Payment, user, *, amount: object = None, reason: str = ""
) -> PaymentRefund:
    order = lock_live_order(payment.order_id, user)
    payment = (
        Payment.objects.select_for_update()
        .get(pk=payment.pk)
    )
    payment.order = order
    assert_payment_refundable(payment)
    reason = required_refund_reason(reason)
    value = validated_refund_amount(payment, amount)
    refund = PaymentRefund.objects.create(
        payment=payment,
        amount=value,
        method="cash",
        status="completed",
        reason=reason[:500],
        requested_by=user,
        completed_at=timezone.now(),
    )
    sync_refund_totals(payment, order)
    log_event(
        "payment",
        f"Возврат из кассы {value} {payment.order.currency}",
        user=user,
        order=payment.order,
        payload={
            "action": "cash_refund_completed",
            "payment_id": payment.pk,
            "refund_id": refund.pk,
            "amount": str(value),
            "reason": reason[:500],
        },
    )
    return refund
