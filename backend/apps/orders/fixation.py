"""Фиксация статуса и оплаты заказа задним числом.

Исторические заказы вносят ради долгов и выручки: остатки склада к этому
моменту уже сверены вручную, поэтому отгрузка здесь НЕ списывает склад —
в отличие от обычного пути ``apps.shipments.services._do_ship``.

Одна операция обслуживает два входа:
* ``POST /orders/`` с ``backdate`` — новый заказ сразу получает дату,
  статус и оплату;
* ``POST /orders/{id}/fixate/`` — то же для уже существующего заказа
  (дата создания при этом не переписывается).
"""
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.eventlog.services import log_event

from .backdate import backdate_events, backdate_moment
from .debt import order_remaining
from .models import Order, Payment
from .statuses import AWAITING_SHIPMENT_STATUSES, is_payment_open, public_status_label

FIXATION_STATUSES = ("confirmed", "shipped")
# Фиксация начинается с подтверждённого заказа: без цен и отдела нет денег.
_CONFIRMATION_REQUIRED = {
    "detail": "Сначала подтвердите заказ — у него должны быть цены и отдел",
    "code": "order_confirmation_required",
}


class OrderFixationSerializer(serializers.Serializer):
    """Параметры фиксации: дата, целевой статус и оплата целиком."""

    date = serializers.DateField()
    status = serializers.ChoiceField(choices=FIXATION_STATUSES, required=False, allow_null=True)
    paid = serializers.BooleanField(required=False, default=False)
    payment_method = serializers.ChoiceField(
        choices=Payment.SETTLED_ON_RECORD, required=False, default="cash",
    )

    def validate_date(self, value: date) -> date:
        if value > timezone.localdate():
            raise ValidationError("Дата не может быть в будущем")
        return value


def assert_can_fixate(user, *, paid: bool) -> None:
    if not user.has_perm_code("orders.edit"):
        raise PermissionDenied("Фиксация статуса доступна только с правом изменения заказов")
    if paid and not user.has_perm_code("payments.create"):
        raise PermissionDenied("Фиксация оплаты доступна только с правом приёма оплат")


def _fix_shipped(order: Order, moment: datetime, user) -> None:
    from apps.shipments.models import Shipment
    from apps.shipments.services import estimated_load_kg, mark_order_shipped

    if order.status == "shipped":
        raise ValidationError({"detail": "Заказ уже отгружен", "code": "already_shipped"})
    if order.status not in AWAITING_SHIPMENT_STATUSES:
        raise ValidationError(_CONFIRMATION_REQUIRED)
    bags = order.ordered_bags
    shipment, _ = Shipment.objects.get_or_create(order=order)
    if order.transport_type == "truck" and shipment.weigh_in_kg is None:
        shipment.weigh_in_kg = estimated_load_kg(order)
    shipment.arrived_at = shipment.arrived_at or moment
    shipment.loading_started_at = shipment.loading_started_at or moment
    shipment.shipped_at = moment
    shipment.bags_loaded = bags
    shipment.save()
    debt_event = mark_order_shipped(order, user)
    # Оперативная сводка считает отгрузки по дню события «shipment» — события
    # отгрузки и долга переносим на указанную дату; аудит остаётся в order_backdated.
    event = log_event(
        "shipment",
        f"Отгрузка зафиксирована задним числом ({moment.date().isoformat()}): {bags} мешков, склад не списан",
        user=user,
        order=order,
        payload={
            "bags_loaded": bags,
            "amount": str(order.total_amount),
            "settlement_intent": order.settlement_intent,
            "source": "fixation",
            "stock_deducted": False,
        },
    )
    backdate_events([event, debt_event], moment)


def _fix_paid(order: Order, moment: datetime, method: str, user) -> None:
    from .services import record_staff_payment

    remaining = order_remaining(order)
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    payment = record_staff_payment(
        order, remaining, user, method=method, note="Зафиксировано задним числом",
    )
    Payment.objects.filter(pk=payment.pk).update(
        paid_at=moment, received_at=moment, confirmed_at=moment,
    )
    # Журнал кассы и сводки по дням читают события оплаты по created_at.
    from apps.eventlog.models import EventLog

    backdate_events(
        EventLog.objects.filter(order=order, event_type="payment", payload__payment_id=payment.pk),
        moment,
    )


@transaction.atomic
def fixate_order(
    order: Order,
    user,
    *,
    date: date,
    status: str | None = None,
    paid: bool = False,
    payment_method: str = "cash",
    set_created: bool = False,
) -> Order:
    """Проставить заказу дату, статус и оплату одной операцией."""
    from .services import lock_live_order

    assert_can_fixate(user, paid=paid)
    order = lock_live_order(order, user)
    moment = backdate_moment(date)

    if status == "shipped":
        _fix_shipped(order, moment, user)
    elif status == "confirmed" and order.status != "confirmed":
        raise ValidationError(_CONFIRMATION_REQUIRED)
    if paid:
        # Способ фиксации — всегда деньги у кассы, поэтому оплату можно
        # зафиксировать и предоплатой у подтверждённого заказа.
        if not is_payment_open(order.status, method=payment_method):
            raise ValidationError({
                "detail": "Оплату можно зафиксировать только у подтверждённого или отгруженного заказа",
                "code": "payment_not_open",
            })
        _fix_paid(order, moment, payment_method, user)
    if set_created:
        Order.objects.filter(pk=order.pk).update(created_at=moment)
        order.created_at = moment

    log_event(
        "order_backdated",
        f"Заказ зафиксирован задним числом: {date.isoformat()}"
        + (f", {public_status_label(status)}" if status else "")
        + (", оплачен" if paid else ""),
        user=user,
        order=order,
        payload={
            "date": date.isoformat(),
            "status": status,
            "paid": paid,
            "payment_method": payment_method if paid else None,
            "created_at_set": set_created,
        },
    )
    order.refresh_from_db()
    return order
