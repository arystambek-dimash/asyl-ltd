"""Сводный отчёт бухгалтерии: касса (поступления), отгрузки и долги.

Правила счёта — единые для всех цифр отчёта:
- Поступление — только оплата, подтверждённая кассой (status=confirmed);
  день поступления — дата подтверждения (confirmed_at), а не дата записи.
- Отгрузка — заказ в статусе shipped; день — фактический выезд
  (shipment.shipped_at). Заказ, переведённый в shipped вручную, Shipment
  не имеет — он ложится на день создания.
- Служебный метод оплаты "debt" деньгами не является и в кассу не входит.
- Удалённые (корзина) заказы не участвуют нигде: скоуп строится от
  Order.objects (LiveOrderManager).
"""
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate

from apps.common.money import money_string as _d
from .models import Order, OrderItem, Payment

CASH_METHODS = ("cash",)
CASHLESS_METHODS = ("card", "kaspi", "invoice")
MONEY_METHODS = CASH_METHODS + CASHLESS_METHODS

_ZERO = Decimal("0")
_MONEY = DecimalField(max_digits=14, decimal_places=2)

def _day_bounds(qs, date_from, date_to):
    if date_from:
        qs = qs.filter(day__gte=date_from)
    if date_to:
        qs = qs.filter(day__lte=date_to)
    return qs


def _income_by_day(orders_qs, date_from, date_to):
    """Подтверждённые кассой оплаты по дням: наличные / безналичные."""
    qs = (Payment.objects
          .filter(status="confirmed", method__in=MONEY_METHODS,
                  order__in=orders_qs)
          .annotate(day=TruncDate(Coalesce("confirmed_at", "paid_at"))))
    qs = _day_bounds(qs, date_from, date_to)
    return qs.values("day").annotate(
        cash=Coalesce(Sum("amount", filter=Q(method__in=CASH_METHODS)), _ZERO,
                      output_field=_MONEY),
        cashless=Coalesce(Sum("amount", filter=Q(method__in=CASHLESS_METHODS)), _ZERO,
                          output_field=_MONEY),
        payments=Count("id"),
    )


def _shipped_by_day(orders_qs, date_from, date_to):
    """Отгрузки по дням: сумма, мешки, заказы и сколько из этого ушло в долг."""
    line = F("quantity") * Coalesce(F("unit_price"), F("product__price"))
    qs = (OrderItem.objects
          .filter(order__in=orders_qs.filter(status="shipped"))
          .annotate(day=TruncDate(Coalesce("order__shipment__shipped_at",
                                           "order__created_at"))))
    qs = _day_bounds(qs, date_from, date_to)
    return qs.values("day").annotate(
        revenue=Coalesce(Sum(line, output_field=_MONEY), _ZERO, output_field=_MONEY),
        bags=Coalesce(Sum("quantity"), 0),
        orders=Count("order", distinct=True),
        debt_amount=Coalesce(
            Sum(line, filter=Q(order__settlement_intent="debt"), output_field=_MONEY),
            _ZERO, output_field=_MONEY),
    )


def _debt_now(orders_qs):
    """Снапшот дебиторки на сейчас — по тем же правилам, что Order.is_debt."""
    orders = (orders_qs.filter(status="shipped", settlement_intent="debt")
              .prefetch_related("items__product", "payments"))
    total = _ZERO
    count = 0
    for order in orders:
        remaining = order.remaining_amount
        if remaining > 0:
            total += remaining
            count += 1
    return {"total": _d(total), "orders": count}


def summary_report(orders_qs, date_from=None, date_to=None) -> dict:
    """Собрать отчёт по «живым» заказам скоупа: дни, итоги, долг."""
    days: dict = {}

    def day_row(day):
        return days.setdefault(day, {
            "date": day.isoformat(),
            "orders": 0, "bags": 0,
            "revenue": _ZERO, "debt_amount": _ZERO,
            "cash": _ZERO, "cashless": _ZERO, "payments": 0,
        })

    for r in _shipped_by_day(orders_qs, date_from, date_to):
        row = day_row(r["day"])
        row["orders"] = r["orders"]
        row["bags"] = r["bags"]
        row["revenue"] = r["revenue"]
        row["debt_amount"] = r["debt_amount"]

    for r in _income_by_day(orders_qs, date_from, date_to):
        row = day_row(r["day"])
        row["cash"] = r["cash"]
        row["cashless"] = r["cashless"]
        row["payments"] = r["payments"]

    total = {
        "revenue": _ZERO, "bags": 0, "orders": 0, "debt_amount": _ZERO,
        "cash": _ZERO, "cashless": _ZERO, "payments": 0,
    }
    for row in days.values():
        for key in total:
            total[key] += row[key]

    day_list = [
        {**row,
         "revenue": _d(row["revenue"]), "debt_amount": _d(row["debt_amount"]),
         "cash": _d(row["cash"]), "cashless": _d(row["cashless"]),
         "received": _d(row["cash"] + row["cashless"])}
        for row in sorted(days.values(), key=lambda r: r["date"], reverse=True)
    ]

    received_total = total["cash"] + total["cashless"]
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "income": {
            "total": _d(received_total),
            "cash": _d(total["cash"]),
            "cashless": _d(total["cashless"]),
            "payments": total["payments"],
        },
        "shipped": {
            "revenue": _d(total["revenue"]),
            "orders": total["orders"],
            "bags": total["bags"],
            "debt_amount": _d(total["debt_amount"]),
        },
        "debt_now": _debt_now(orders_qs),
        "days": day_list,
    }
