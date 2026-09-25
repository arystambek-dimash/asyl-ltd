"""Сводный отчёт бухгалтерии: касса, отгрузки и текущие остатки.

Правила счёта:
- подтверждённая оплата — приход на ``Payment.RECOGNIZED_AT`` (подтверждение);
- завершённый возврат — расход на ``PaymentRefund.RECOGNIZED_AT`` (завершение);
- отгрузка относится к ``shipment.shipped_at`` (для legacy без Shipment — к
  ``Order.created_at``);
- периодический долг — текущий непогашенный остаток отгрузок выбранного
  периода, а не первоначальный ``settlement_intent``;
- служебный способ ``Payment.NON_MONEY_METHODS`` деньгами не является;
- удалённые заказы исключены исходным ``Order.objects`` queryset.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.clients.models import Store
from apps.clients.services import is_store_overdue
from apps.common.money import (
    DEFAULT_CURRENCY,
    as_money_strings,
    primary_currency,
    sum_by_currency,
)
from apps.common.money import money_string as _d
from apps.sales.labels import department_label
from apps.sales.models import Department

from .debt import DEBT_STATUS, debt_orders, order_remaining, payment_status
from .labels import payment_method_label
from .models import Order, Payment, PaymentRefund
from .querysets import order_department

# Наличные — только касса; всё остальное (Kaspi, счёт, удалённо, карта,
# возвраты через ApiPay) — безнал.
CASH_METHODS = ("cash",)

_ZERO = Decimal(0)
_MONEY = DecimalField(max_digits=14, decimal_places=2)
# Денежные поля отгрузок: снимок заказа, строки клиентов, дней и итог.
_SHIPPED_FIELDS = ("revenue", "paid_amount", "debt_amount")


def _day_bounds(qs, date_from, date_to):
    if date_from:
        qs = qs.filter(day__gte=date_from)
    if date_to:
        qs = qs.filter(day__lte=date_to)
    return qs


def _payment_events_by_day(orders_qs, date_from, date_to):
    """Gross money receipts, stamped by the actual confirmation event."""
    qs = (
        Payment.objects.filter(status="confirmed", order__in=orders_qs)
        .exclude(method__in=Payment.NON_MONEY_METHODS)
        .annotate(day=TruncDate(Payment.RECOGNIZED_AT))
    )
    qs = _day_bounds(qs, date_from, date_to)
    return (
        qs.order_by()
        .annotate(department_code=order_department("order__"))
        .values("day", "order__currency", "department_code", "method")
        .annotate(
            gross_cash=Coalesce(
                Sum("amount", filter=Q(method__in=CASH_METHODS)),
                _ZERO,
                output_field=_MONEY,
            ),
            gross_cashless=Coalesce(
                Sum("amount", filter=~Q(method__in=CASH_METHODS)),
                _ZERO,
                output_field=_MONEY,
            ),
            payments=Count("id"),
        )
    )


def _refund_events_by_day(orders_qs, date_from, date_to):
    """Completed refund outflows, stamped by completion rather than payment day."""
    qs = (
        PaymentRefund.objects.filter(
            status="completed",
            payment__order__in=orders_qs,
        )
        .exclude(payment__method__in=Payment.NON_MONEY_METHODS)
        .annotate(day=TruncDate(PaymentRefund.RECOGNIZED_AT))
    )
    qs = _day_bounds(qs, date_from, date_to)
    return (
        qs.order_by()
        .annotate(department_code=order_department("payment__order__"))
        .values(
            "day",
            "payment__order__currency",
            "department_code",
            "payment__method",
        )
        .annotate(
            refund_cash=Coalesce(
                Sum("amount", filter=Q(method__in=CASH_METHODS)),
                _ZERO,
                output_field=_MONEY,
            ),
            refund_cashless=Coalesce(
                Sum("amount", filter=~Q(method__in=CASH_METHODS)),
                _ZERO,
                output_field=_MONEY,
            ),
            refunds=Count("id"),
        )
    )


def _period_shipped_snapshots(orders_qs, date_from, date_to) -> list[dict]:
    """Load period shipments once and calculate their current financial split.

    Items and payments are prefetched in two bulk queries. Consequently using
    ``Order.total_amount``/``paid_total`` below never creates an N+1 and avoids
    the item x payment multiplication produced by a naive joined aggregate.
    """
    qs = (
        orders_qs.filter(status="shipped")
        .annotate(day=TruncDate(Order.SALE_AT), department_code=order_department())
        .select_related("client__user")
        .prefetch_related("items", "payments")
    )
    qs = _day_bounds(qs, date_from, date_to)

    result = []
    for order in qs:
        total = max(_ZERO, order.total_amount)
        paid = max(_ZERO, order.paid_total)
        allocated_paid = min(paid, total)
        remaining = order_remaining(order)
        result.append({
            "order": order,
            "day": order.day,
            "currency": order.currency or DEFAULT_CURRENCY,
            "bags": order.ordered_bags,
            "revenue": total,
            "paid_amount": allocated_paid,
            "remaining_amount": remaining,
            "debt_amount": remaining,
            # По факту денег, а не по сохранённому payment_status (он может отстать).
            "payment_status": payment_status(total, paid),
        })
    return result


def _clients_breakdown(snapshots: list[dict]):
    """Current paid/debt split for shipments in the selected period."""
    clients: dict = {}
    for snapshot in snapshots:
        order = snapshot["order"]
        currency = snapshot["currency"]
        entry = clients.setdefault(order.client_id, {
            "id": order.client_id,
            "name": order.client.name,
            "orders": 0,
            "bags": 0,
            **{f"{field}_by_currency": defaultdict(lambda: _ZERO) for field in _SHIPPED_FIELDS},
            "order_list": [],
        })
        entry["orders"] += 1
        entry["bags"] += snapshot["bags"]
        for field in _SHIPPED_FIELDS:
            entry[f"{field}_by_currency"][currency] += snapshot[field]
        entry["order_list"].append({
            "id": order.id,
            "date": snapshot["day"].isoformat(),
            "bags": snapshot["bags"],
            "total": _d(snapshot["revenue"]),
            "remaining_amount": _d(snapshot["remaining_amount"]),
            "currency": currency,
            "payment_status": snapshot["payment_status"],
        })

    result = list(clients.values())
    for entry in result:
        entry["order_list"].sort(
            key=lambda order: (order["date"], order["id"]),
            reverse=True,
        )
        currency = primary_currency(entry["revenue_by_currency"])
        entry["currency"] = currency
        for field in _SHIPPED_FIELDS:
            source = f"{field}_by_currency"
            entry[field] = _d(entry[source].get(currency, _ZERO))
            entry[source] = as_money_strings(entry[source])
    # Валюты не ранжируем друг против друга без курса (как список должников):
    # сначала группа основной валюты клиента, затем выручка по убыванию.
    result.sort(key=lambda entry: (
        entry["currency"],
        -Decimal(entry["revenue"]),
        entry["name"].casefold(),
    ))
    return result


def _debt_now(orders_qs):
    """Снапшот дебиторки на сейчас — по правилам orders/debt.py."""
    orders = list(
        orders_qs.filter(status=DEBT_STATUS)
        .prefetch_related("items", "payments")
    )
    outstanding = debt_orders(orders)
    totals = sum_by_currency(outstanding, order_remaining)
    currency = primary_currency(totals)

    store_ids = {order.store_id for order in outstanding if order.store_id}
    today = timezone.localdate()
    overdue_store_ids = {
        store.id
        for store in Store.objects.filter(id__in=store_ids)
        if is_store_overdue(store, today)
    }
    overdue_orders = [
        order for order in outstanding if order.store_id in overdue_store_ids
    ]
    overdue_totals = sum_by_currency(overdue_orders, order_remaining)
    overdue_currency = primary_currency(overdue_totals)

    return {
        "total": _d(totals.get(currency, _ZERO)),
        "by_currency": as_money_strings(totals),
        "currency": currency,
        "orders": len(outstanding),
        "overdue_by_currency": as_money_strings(overdue_totals),
        "overdue_currency": overdue_currency,
        "overdue_clients": len({order.client_id for order in overdue_orders}),
    }


def _shipping_currency_row():
    return dict.fromkeys(_SHIPPED_FIELDS, _ZERO)


def _income_currency_row():
    return {
        "gross_cash": _ZERO,
        "gross_cashless": _ZERO,
        "refund_cash": _ZERO,
        "refund_cashless": _ZERO,
    }


def _income_split(rows_by_currency: dict) -> dict[str, dict[str, Decimal]]:
    """Нал/безнал/приход/возвраты/нетто по валютам из сумм оплат и возвратов.

    Нал и безнал — уже за вычетом возвратов тем же каналом; ``net`` — всё
    полученное за вычетом всех возвратов.
    """
    split = {key: {} for key in ("cash", "cashless", "gross", "refunded", "net")}
    for currency, row in rows_by_currency.items():
        gross = row["gross_cash"] + row["gross_cashless"]
        refunded = row["refund_cash"] + row["refund_cashless"]
        split["cash"][currency] = row["gross_cash"] - row["refund_cash"]
        split["cashless"][currency] = row["gross_cashless"] - row["refund_cashless"]
        split["gross"][currency] = gross
        split["refunded"][currency] = refunded
        split["net"][currency] = gross - refunded
    return split


class _ReportRows:
    """Строки дней и отделов: их пополняют отгрузки, оплаты и возвраты."""

    def __init__(self):
        self.days: dict = {}
        self.departments: dict = {}

    def day(self, day):
        return self.days.setdefault(day, {
            "date": day.isoformat(),
            "orders": 0,
            "bags": 0,
            "payments": 0,
            "refunds": 0,
            **{f"{field}_by_currency": defaultdict(lambda: _ZERO) for field in _SHIPPED_FIELDS},
            "income": defaultdict(_income_currency_row),
        })

    def department(self, code):
        return self.departments.setdefault(code, {
            "code": code,
            "orders": 0,
            "payments": 0,
            "sales_by_currency": defaultdict(lambda: _ZERO),
            "received_by_currency": defaultdict(lambda: _ZERO),
            "refunded_by_currency": defaultdict(lambda: _ZERO),
        })


def _shipped_section(snapshots: list[dict], rows: _ReportRows) -> dict:
    """Отгрузки периода: итог в основной валюте, раскладка по валютам, дни и отделы."""
    shipped_by_currency = defaultdict(_shipping_currency_row)
    total_bags = 0
    for snapshot in snapshots:
        currency = snapshot["currency"]
        department = rows.department(snapshot["order"].department_code)
        department["orders"] += 1
        department["sales_by_currency"][currency] += snapshot["revenue"]
        day = rows.day(snapshot["day"])
        day["orders"] += 1
        day["bags"] += snapshot["bags"]
        total_bags += snapshot["bags"]
        for field in _SHIPPED_FIELDS:
            day[f"{field}_by_currency"][currency] += snapshot[field]
            shipped_by_currency[currency][field] += snapshot[field]

    by_currency = {
        field: {currency: values[field] for currency, values in shipped_by_currency.items()}
        for field in _SHIPPED_FIELDS
    }
    currency = primary_currency(by_currency["revenue"])
    totals = shipped_by_currency.get(currency, _shipping_currency_row())
    return {
        **{field: _d(totals[field]) for field in _SHIPPED_FIELDS},
        "orders": len(snapshots),
        "bags": total_bags,
        "currency": currency,
        **{
            f"{field}_by_currency": as_money_strings(by_currency[field])
            for field in _SHIPPED_FIELDS
        },
    }


def _income_section(orders_qs, date_from, date_to, rows: _ReportRows) -> dict:
    """Касса периода: приход по дню подтверждения, возврат — по дню завершения."""
    income_by_currency = defaultdict(_income_currency_row)
    # {валюта: {способ: нетто}} — возврат вычитается из способа исходной
    # оплаты, как paid_by_method в транзакциях; нал/безнал считаются как раньше.
    income_by_method = defaultdict(lambda: defaultdict(lambda: _ZERO))
    payments_by_method = defaultdict(int)
    payments_total = 0
    for event in _payment_events_by_day(orders_qs, date_from, date_to):
        currency = event["order__currency"] or DEFAULT_CURRENCY
        gross = event["gross_cash"] + event["gross_cashless"]
        department = rows.department(event["department_code"])
        department["received_by_currency"][currency] += gross
        department["payments"] += event["payments"]
        day = rows.day(event["day"])
        day["payments"] += event["payments"]
        for income in (day["income"][currency], income_by_currency[currency]):
            income["gross_cash"] += event["gross_cash"]
            income["gross_cashless"] += event["gross_cashless"]
        income_by_method[currency][event["method"]] += gross
        payments_by_method[event["method"]] += event["payments"]
        payments_total += event["payments"]

    refunds_total = 0
    for event in _refund_events_by_day(orders_qs, date_from, date_to):
        currency = event["payment__order__currency"] or DEFAULT_CURRENCY
        refunded = event["refund_cash"] + event["refund_cashless"]
        rows.department(event["department_code"])["refunded_by_currency"][
            currency
        ] += refunded
        day = rows.day(event["day"])
        day["refunds"] += event["refunds"]
        for income in (day["income"][currency], income_by_currency[currency]):
            income["refund_cash"] += event["refund_cash"]
            income["refund_cashless"] += event["refund_cashless"]
        income_by_method[currency][event["payment__method"]] -= refunded
        refunds_total += event["refunds"]

    split = _income_split(income_by_currency)
    currency = primary_currency({
        code: split["gross"][code] + split["refunded"][code]
        for code in income_by_currency
    })
    return {
        "total": _d(split["net"].get(currency, _ZERO)),
        "gross": _d(split["gross"].get(currency, _ZERO)),
        "refunded": _d(split["refunded"].get(currency, _ZERO)),
        "cash": _d(split["cash"].get(currency, _ZERO)),
        "cashless": _d(split["cashless"].get(currency, _ZERO)),
        "payments": payments_total,
        "refunds": refunds_total,
        "currency": currency,
        "by_currency": as_money_strings(split["net"]),
        "gross_by_currency": as_money_strings(split["gross"]),
        "refunded_by_currency": as_money_strings(split["refunded"]),
        "cash_by_currency": as_money_strings(split["cash"]),
        "cashless_by_currency": as_money_strings(split["cashless"]),
        "by_method_by_currency": {
            code: as_money_strings(dict(methods))
            for code, methods in income_by_method.items()
        },
        "payments_by_method": dict(payments_by_method),
        "method_labels": {
            method: payment_method_label(method)
            for methods in income_by_method.values()
            for method in methods
        },
    }


def _department_rows(departments: dict, *, income_only: bool) -> list[dict]:
    # Reuse the same shipment/payment/refund rows as the total report. Separate
    # grouping keys avoid multiplying items by payments and add no per-department queries.
    labels = {row.code: row for row in Department.objects.all()} if departments else {}
    result = []
    for code, values in sorted(departments.items()):
        name, color = department_label(code, labels.get(code))
        received = values["received_by_currency"]
        refunded = values["refunded_by_currency"]
        result.append({
            "code": code,
            "name": name,
            "color": color,
            "orders": values["orders"] if not income_only else None,
            "payments": values["payments"],
            "sales_by_currency": (
                as_money_strings(values["sales_by_currency"])
                if not income_only
                else None
            ),
            "received_by_currency": as_money_strings(received),
            "refunded_by_currency": as_money_strings(refunded),
            "net_by_currency": as_money_strings({
                currency: received.get(currency, _ZERO) - refunded.get(currency, _ZERO)
                for currency in received.keys() | refunded.keys()
            }),
        })
    return result


def _day_rows(days: dict, *, revenue_currency: str, income_currency: str) -> list[dict]:
    """Дни от новых к старым: отгрузки в основной валюте отгрузок, касса — в валюте кассы."""
    result = []
    for row in sorted(days.values(), key=lambda value: value["date"], reverse=True):
        split = _income_split(row["income"])
        result.append({
            "date": row["date"],
            "orders": row["orders"],
            "bags": row["bags"],
            **{
                field: _d(row[f"{field}_by_currency"].get(revenue_currency, _ZERO))
                for field in _SHIPPED_FIELDS
            },
            "cash": _d(split["cash"].get(income_currency, _ZERO)),
            "cashless": _d(split["cashless"].get(income_currency, _ZERO)),
            "received": _d(split["net"].get(income_currency, _ZERO)),
            "refunded": _d(split["refunded"].get(income_currency, _ZERO)),
            "payments": row["payments"],
            "refunds": row["refunds"],
            **{
                f"{field}_by_currency": as_money_strings(row[f"{field}_by_currency"])
                for field in _SHIPPED_FIELDS
            },
            "cash_by_currency": as_money_strings(split["cash"]),
            "cashless_by_currency": as_money_strings(split["cashless"]),
            "received_by_currency": as_money_strings(split["net"]),
            "refunded_by_currency": as_money_strings(split["refunded"]),
        })
    return result


def summary_report(orders_qs, date_from=None, date_to=None, *, income_only=False) -> dict:
    """Собрать отчёт по живым заказам скоупа."""
    rows = _ReportRows()
    snapshots = [] if income_only else _period_shipped_snapshots(orders_qs, date_from, date_to)
    shipped = _shipped_section(snapshots, rows)
    income = _income_section(orders_qs, date_from, date_to, rows)
    report = {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "income": income,
        "departments": _department_rows(rows.departments, income_only=income_only),
    }
    if income_only:
        return report
    return {
        **report,
        "shipped": shipped,
        "debt_now": _debt_now(orders_qs),
        "clients": _clients_breakdown(snapshots),
        "days": _day_rows(
            rows.days,
            revenue_currency=shipped["currency"],
            income_currency=income["currency"],
        ),
    }
