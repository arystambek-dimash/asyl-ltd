"""Операции задним числом: какой момент дня писать и как перенести журнал.

Отчёты по дням читают отгрузки и долги по событиям журнала (``created_at``),
а не по моменту записи в CRM. Заказ, зафиксированный задним числом
(:mod:`apps.orders.fixation`), и отгрузка вагонов по вчерашнему отчёту
(:func:`apps.shipments.services.ship_rail_report`) переносят свои события на
день операции — одним способом.
"""
from datetime import date, datetime, time

from django.utils import timezone


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
