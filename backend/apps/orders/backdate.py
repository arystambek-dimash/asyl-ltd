"""Операции задним числом: какой момент дня писать и как перенести журнал.

Отчёты по дням читают отгрузки и долги по событиям журнала (``created_at``),
а не по моменту записи в CRM. Заказ, зафиксированный задним числом
(:mod:`apps.orders.fixation`), и отгрузка вагонов по вчерашнему отчёту
(:func:`apps.shipments.services.ship_rail_report`) переносят свои события на
день операции — одним способом. Так же оплата, принятая кассой «за вчера»
(:func:`payment_moment`, :func:`apps.orders.services.record_staff_payment`).
"""
from datetime import date, datetime, time

from django.utils import timezone
from rest_framework.exceptions import ValidationError


def backdate_moment(day: date) -> datetime:
    """Полдень указанного дня в локальном поясе: день однозначен во всех отчётах."""
    return timezone.make_aware(datetime.combine(day, time(12, 0)))


def backdate_events(events, moment: datetime) -> None:
    """Перенести события журнала на ``moment``.

    Журнал неизменяем (:meth:`EventLog.save`): у записи меняется только дата,
    и только запросом ``update`` — сообщение и автор остаются как были.
    """
    from apps.eventlog.models import EventLog

    ids = [event.pk for event in events if event is not None]
    if ids:
        EventLog.objects.filter(pk__in=ids).update(created_at=moment)


def payment_day(raw) -> date | None:
    """День оплаты из запроса кассы: пусто — сегодня (``None``), будущее — отказ."""
    if raw in (None, ""):
        return None
    try:
        day = date.fromisoformat(str(raw))
    except ValueError:
        raise ValidationError({"detail": "Дата оплаты — в формате ГГГГ-ММ-ДД", "code": "bad_payment_date"}) from None
    if day > timezone.localdate():
        raise ValidationError({"detail": "Дата оплаты не может быть в будущем", "code": "payment_date_in_future"})
    return day


def order_start(order) -> datetime:
    """С какого момента у заказа бывают деньги: создание, а у внесённого задним
    числом существующего заказа — его отгрузка (она раньше даты создания)."""
    from django.core.exceptions import ObjectDoesNotExist

    try:
        shipped_at = order.shipment.shipped_at
    except ObjectDoesNotExist:
        shipped_at = None
    return min(moment for moment in (order.created_at, shipped_at) if moment)


def payment_moment(day: date | None, orders=()) -> datetime | None:
    """Когда записать оплату: сегодня — как обычно (``None``), прошлый день — его
    полдень, но не раньше создания и отгрузки заказов в тот же день."""
    if day is None or day == timezone.localdate():
        return None
    from django.core.exceptions import ObjectDoesNotExist

    moment = backdate_moment(day)
    for order in orders:
        try:
            shipped_at = order.shipment.shipped_at
        except ObjectDoesNotExist:
            shipped_at = None
        # Создан или отгружен в тот же день позже полудня — оплата после этого:
        # иначе в выписке погашение встанет раньше самой продажи.
        for stamp in (order.created_at, shipped_at):
            if stamp and timezone.localdate(stamp) == day and stamp > moment:
                moment = stamp
    return moment


def assert_paid_after_order(order, day: date) -> None:
    """Оплата не бывает раньше самого заказа — защита от опечатки в дате."""
    start = timezone.localdate(order_start(order))
    if day < start:
        raise ValidationError({
            "detail": f"Заказ #{order.pk} — от {start:%d.%m.%Y}, оплату раньше этой даты не записать",
            "code": "payment_before_order",
        })


def log_payment_backdated(payment, day: date, user) -> None:
    """Аудит: кто и когда записал оплату прошлым днём (само событие — сегодняшнее)."""
    from apps.eventlog.services import log_event

    log_event(
        "order_backdated",
        f"Оплата {payment.amount} {payment.order.currency} записана датой {day:%d.%m.%Y}",
        user=user,
        order=payment.order,
        payload={"date": day.isoformat(), "status": None, "paid": True,
                 "payment_method": payment.method, "payment_id": payment.pk},
    )

