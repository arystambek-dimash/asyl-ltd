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
from collections import defaultdict
from decimal import Decimal
from typing import TypedDict

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate

from apps.common.money import money_string as _d
from .debt import (
    DEFAULT_CURRENCY, as_money_strings, debt_orders, order_remaining,
    primary_currency, sum_by_currency,
)
from .models import OrderItem, Payment

CASH_METHODS = ("cash",)
CASHLESS_METHODS = ("card", "kaspi", "invoice")
MONEY_METHODS = CASH_METHODS + CASHLESS_METHODS

_ZERO = Decimal("0")
_MONEY = DecimalField(max_digits=14, decimal_places=2)


class _ReportTotals(TypedDict):
    revenue: Decimal
    bags: int
    orders: int
    debt_amount: Decimal
    cash: Decimal
    cashless: Decimal
    payments: int

def _day_bounds(qs, date_from, date_to):
    if date_from:
        qs = qs.filter(day__gte=date_from)
    if date_to:
        qs = qs.filter(day__lte=date_to)
    return qs


def _income_by_day(orders_qs, date_from, date_to):
    """Подтверждённые кассой оплаты по дням и валютам: наличные / безналичные."""
    net = F("amount") - F("refunded_amount")
    qs = (Payment.objects
          .filter(status="confirmed", method__in=MONEY_METHODS,
                  order__in=orders_qs)
          .annotate(day=TruncDate(Coalesce("confirmed_at", "paid_at"))))
    qs = _day_bounds(qs, date_from, date_to)
    return qs.values("day", "order__currency").annotate(
        cash=Coalesce(Sum(net, filter=Q(method__in=CASH_METHODS)), _ZERO,
                      output_field=_MONEY),
        cashless=Coalesce(Sum(net, filter=Q(method__in=CASHLESS_METHODS)), _ZERO,
                          output_field=_MONEY),
        payments=Count("id"),
    )


def _shipped_by_day(orders_qs, date_from, date_to):
    """Отгрузки по дням и валютам: сумма, мешки, заказы и доля в долг."""
    line = F("quantity") * Coalesce(
        F("unit_price"), Value(_ZERO), output_field=_MONEY)
    qs = (OrderItem.objects
          .filter(order__in=orders_qs.filter(status="shipped"))
          .annotate(day=TruncDate(Coalesce("order__shipment__shipped_at",
                                           "order__created_at"))))
    qs = _day_bounds(qs, date_from, date_to)
    return qs.values("day", "order__currency").annotate(
        revenue=Coalesce(Sum(line, output_field=_MONEY), _ZERO, output_field=_MONEY),
        bags=Coalesce(Sum("quantity"), 0),
        orders=Count("order", distinct=True),
        debt_amount=Coalesce(
            Sum(line, filter=Q(order__settlement_intent="debt"), output_field=_MONEY),
            _ZERO, output_field=_MONEY),
    )


def _debt_now(orders_qs):
    """Снапшот дебиторки на сейчас — по правилам orders/debt.py.

    Долг считается тем же helper'ом, что и остальные экраны, и раскладывается
    по валютам: 1000 ₸ и 5 $ не дают «1005».
    """
    orders = list(
        orders_qs.filter(status="shipped", settlement_intent="debt")
        .prefetch_related("items", "payments")
    )
    outstanding = debt_orders(orders)
    totals = sum_by_currency(outstanding, order_remaining)
    return {
        "total": _d(sum(totals.values(), _ZERO)),
        "by_currency": as_money_strings(totals),
        "currency": primary_currency(totals),
        "orders": len(outstanding),
    }


def summary_report(orders_qs, date_from=None, date_to=None) -> dict:
    """Собрать отчёт по «живым» заказам скоупа: дни, итоги, долг."""
    days: dict = {}

    def day_row(day):
        return days.setdefault(day, {
            "date": day.isoformat(),
            "orders": 0, "bags": 0,
            "revenue": _ZERO, "debt_amount": _ZERO,
            "cash": _ZERO, "cashless": _ZERO, "payments": 0,
            "revenue_by_currency": defaultdict(lambda: _ZERO),
            "received_by_currency": defaultdict(lambda: _ZERO),
        })

    # Валюты не складываются: суммы копятся отдельно по каждой (KZT/USD), а
    # плоское поле остаётся для совместимости и показывает основную валюту.
    by_currency: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"revenue": _ZERO, "debt_amount": _ZERO,
                 "cash": _ZERO, "cashless": _ZERO}
    )

    def accumulate(currency: str, field: str, value: Decimal) -> None:
        by_currency[currency or DEFAULT_CURRENCY][field] += value

    for r in _shipped_by_day(orders_qs, date_from, date_to):
        row = day_row(r["day"])
        currency = r["order__currency"]
        row["orders"] += r["orders"]
        row["bags"] += r["bags"]
        row["revenue"] += r["revenue"]
        row["debt_amount"] += r["debt_amount"]
        row["revenue_by_currency"][currency] += r["revenue"]
        accumulate(currency, "revenue", r["revenue"])
        accumulate(currency, "debt_amount", r["debt_amount"])

    for r in _income_by_day(orders_qs, date_from, date_to):
        row = day_row(r["day"])
        currency = r["order__currency"]
        row["cash"] += r["cash"]
        row["cashless"] += r["cashless"]
        row["payments"] += r["payments"]
        row["received_by_currency"][currency] += r["cash"] + r["cashless"]
        accumulate(currency, "cash", r["cash"])
        accumulate(currency, "cashless", r["cashless"])

    total: _ReportTotals = {
        "revenue": _ZERO, "bags": 0, "orders": 0, "debt_amount": _ZERO,
        "cash": _ZERO, "cashless": _ZERO, "payments": 0,
    }
    for row in days.values():
        total["revenue"] += row["revenue"]
        total["bags"] += row["bags"]
        total["orders"] += row["orders"]
        total["debt_amount"] += row["debt_amount"]
        total["cash"] += row["cash"]
        total["cashless"] += row["cashless"]
        total["payments"] += row["payments"]

    day_list = [
        {**row,
         "revenue": _d(row["revenue"]), "debt_amount": _d(row["debt_amount"]),
         "cash": _d(row["cash"]), "cashless": _d(row["cashless"]),
         "received": _d(row["cash"] + row["cashless"]),
         "revenue_by_currency": as_money_strings(row["revenue_by_currency"]),
         "received_by_currency": as_money_strings(row["received_by_currency"])}
        for row in sorted(days.values(), key=lambda r: r["date"], reverse=True)
    ]

    received_total = total["cash"] + total["cashless"]
    income_by_currency = {
        currency: sums["cash"] + sums["cashless"]
        for currency, sums in by_currency.items()
    }
    revenue_by_currency = {
        currency: sums["revenue"] for currency, sums in by_currency.items()
    }
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "income": {
            "total": _d(received_total),
            "cash": _d(total["cash"]),
            "cashless": _d(total["cashless"]),
            "payments": total["payments"],
            "currency": primary_currency(income_by_currency),
            "by_currency": as_money_strings(income_by_currency),
            "cash_by_currency": as_money_strings(
                {c: s["cash"] for c, s in by_currency.items()}),
            "cashless_by_currency": as_money_strings(
                {c: s["cashless"] for c, s in by_currency.items()}),
        },
        "shipped": {
            "revenue": _d(total["revenue"]),
            "orders": total["orders"],
            "bags": total["bags"],
            "debt_amount": _d(total["debt_amount"]),
            "currency": primary_currency(revenue_by_currency),
            "revenue_by_currency": as_money_strings(revenue_by_currency),
            "debt_amount_by_currency": as_money_strings(
                {c: s["debt_amount"] for c, s in by_currency.items()}),
        },
        "debt_now": _debt_now(orders_qs),
        "days": day_list,
    }
