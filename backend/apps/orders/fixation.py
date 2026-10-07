"""Фиксация статуса и оплаты заказа задним числом.

Исторические заказы вносят ради долгов и выручки: остатки склада к этому
моменту уже сверены вручную, поэтому отгрузка здесь НЕ списывает склад —
в отличие от обычного пути ``apps.shipments.services._do_ship``. Это помнит
``Shipment.stock_deducted = False``: откат и правка такого заказа склад не
трогают (``apps.shipments.sources.shipment_sources`` → ``not_deducted``).

Одна операция обслуживает два входа:
* ``POST /orders/`` с ``backdate`` — новый заказ сразу получает дату,
  статус и оплату;
* ``POST /orders/{id}/fixate/`` — то же для уже существующего заказа
  (дата создания при этом не переписывается). Уже отгруженному заказу
  суперюзер так же переносит отгрузку на другой день (``_move_shipped``).
"""
from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.eventlog.services import log_event

from .backdate import backdate_events, backdate_moment
from .debt import order_remaining
from .models import Order, Payment
from .statuses import AWAITING_SHIPMENT_STATUSES, is_payment_open, public_status_label

FIXATION_STATUSES = ("confirmed", "shipped")
# События отгрузки, которые переезжают вместе с ней: прибытие и статусы,
# погрузка, камера, долг и сама отгрузка. Деньги остаются в своём дне.
SHIPMENT_EVENT_TYPES = (
    "status", "loading_start", "loading", "loading_done", "camera_bound", "debt", "shipment",
)
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
    # Склад не списан — откат ничего не вернёт, правка не сдвинет остатки.
    shipment.stock_deducted = False
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


def _move_shipped(order: Order, day: date, user) -> date:
    """Перенести уже состоявшуюся отгрузку на другой день — только суперюзер.

    Время суток сохраняется. Вместе с отгрузкой едут её события и дата
    создания заказа, если иначе заказ оказался бы создан после отгрузки.
    Склад и оплаты не трогаем: списание стоит в журнале склада по порядку
    остатков, а деньги принадлежат своему дню кассы. Возвращает прежний день.
    """
    from apps.eventlog.models import EventLog
    from apps.shipments.models import Shipment

    if not user.is_superuser:
        raise ValidationError({"detail": "Заказ уже отгружен", "code": "already_shipped"})
    shipment = Shipment.objects.select_for_update().filter(order=order).first()
    if shipment is None or shipment.shipped_at is None:
        raise ValidationError({"detail": "У заказа нет даты отгрузки — переносить нечего", "code": "shipment_missing"})
    shipped_at = shipment.shipped_at
    old_day = timezone.localtime(shipped_at).date()
    if day == old_day:
        raise ValidationError({"detail": "Отгрузка уже стоит на этой дате", "code": "same_shipment_date"})
    # Сегодняшний день с поздним временем отгрузки не уходит в будущее.
    shift = min(shipped_at + timedelta(days=(day - old_day).days), timezone.now()) - shipped_at
    first = min(moment for moment in (shipment.arrived_at, shipment.loading_started_at, shipped_at) if moment)

    # Только события текущей отгрузки: откатанные раньше — уже история.
    last_rollback = EventLog.objects.filter(
        order=order, event_type="shipment_rollback",
    ).aggregate(last=Max("pk"))["last"] or 0
    events = EventLog.objects.filter(order=order, event_type__in=SHIPMENT_EVENT_TYPES, pk__gt=last_rollback)
    # Обе выборки — по исходным датам и до первого сдвига: иначе событие,
    # уже переехавшее назад, попало бы во вторую выборку и сдвинулось дважды.
    shipment_events = list(events.filter(
        created_at__gte=first, created_at__lte=shipped_at + timedelta(minutes=1),
    ).values_list("pk", flat=True))
    # Заказ не бывает создан после отгрузки: дату создания двигаем только
    # назад — на тот же сдвиг, а если и так поздно, то к началу отгрузки.
    created_at = order.created_at
    created = min(created_at + shift, first + shift) if created_at > first + shift else None
    early_events = list(events.filter(
        created_at__gte=created_at, created_at__lt=first,
    ).values_list("pk", flat=True)) if created else []
    EventLog.objects.filter(pk__in=shipment_events).update(created_at=F("created_at") + shift)
    if created:
        EventLog.objects.filter(pk__in=early_events).update(created_at=F("created_at") + (created - created_at))
        Order.objects.filter(pk=order.pk).update(created_at=created)
    Shipment.objects.filter(pk=shipment.pk).update(
        arrived_at=F("arrived_at") + shift,
        loading_started_at=F("loading_started_at") + shift,
        shipped_at=F("shipped_at") + shift,
    )
    return old_day


def _fix_paid(order: Order, moment: datetime, method: str, user) -> None:
    from .services import record_staff_payment

    remaining = order_remaining(order)
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    record_staff_payment(order, remaining, user, method=method, note="Зафиксировано задним числом", paid_at=moment)


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

    moved_from = None
    if status == "shipped" and order.status == "shipped":
        moved_from = _move_shipped(order, date, user)
    elif status == "shipped":
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
        + (f", отгрузка перенесена с {moved_from:%d.%m.%Y}" if moved_from else "")
        + (", оплачен" if paid else ""),
        user=user,
        order=order,
        payload={
            "date": date.isoformat(),
            "status": status,
            "paid": paid,
            "payment_method": payment_method if paid else None,
            "created_at_set": set_created,
            # Ключ только у переноса: по нему грузчику закрыта отмена такой отгрузки.
            **({"shipment_moved_from": moved_from.isoformat()} if moved_from else {}),
        },
    )
    order.refresh_from_db()
    return order
