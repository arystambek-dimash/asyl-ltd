"""Сводный отчёт бухгалтерии: касса, отгрузки и текущие остатки.

Правила счёта:
- подтверждённая оплата — приход на ``confirmed_at`` (fallback: ``paid_at``);
- завершённый возврат — расход на ``PaymentRefund.completed_at``;
- отгрузка относится к ``shipment.shipped_at`` (для legacy без Shipment — к
  ``Order.created_at``);
- периодический долг — текущий непогашенный остаток отгрузок выбранного
  периода, а не первоначальный ``settlement_intent``;
- служебный метод оплаты ``debt`` деньгами не является;
- удалённые заказы исключены исходным ``Order.objects`` queryset.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.common.money import (
    DEFAULT_CURRENCY,
    as_money_strings,
    primary_currency,
    sum_by_currency,
)
from apps.common.money import money_string as _d
from apps.sales.models import Department

from .debt import debt_orders, order_remaining
from .models import Payment, PaymentRefund

CASH_METHODS = ("cash",)
CASHLESS_METHODS = ("card", "kaspi", "invoice")
MONEY_METHODS = CASH_METHODS + CASHLESS_METHODS

REFUND_CASH_METHODS = ("cash",)
REFUND_CASHLESS_METHODS = ("apipay",)
REFUND_METHODS = REFUND_CASH_METHODS + REFUND_CASHLESS_METHODS

_ZERO = Decimal(0)
_MONEY = DecimalField(max_digits=14, decimal_places=2)


def _day_bounds(qs, date_from, date_to):
    if date_from:
        qs = qs.filter(day__gte=date_from)
    if date_to:
        qs = qs.filter(day__lte=date_to)
    return qs


def _payment_events_by_day(orders_qs, date_from, date_to):
    """Gross money receipts, stamped by the actual confirmation event."""
    qs = (
        Payment.objects.filter(
            status="confirmed",
            method__in=MONEY_METHODS,
            order__in=orders_qs,
        )
        .annotate(day=TruncDate(Coalesce("confirmed_at", "paid_at")))
    )
    qs = _day_bounds(qs, date_from, date_to)
    return (
        qs.order_by()
        .values("day", "order__currency", "order__department", "method")
        .annotate(
            gross_cash=Coalesce(
                Sum("amount", filter=Q(method__in=CASH_METHODS)),
                _ZERO,
                output_field=_MONEY,
            ),
            gross_cashless=Coalesce(
                Sum("amount", filter=Q(method__in=CASHLESS_METHODS)),
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
            completed_at__isnull=False,
            method__in=REFUND_METHODS,
            payment__order__in=orders_qs,
        )
        .annotate(day=TruncDate("completed_at"))
    )
    qs = _day_bounds(qs, date_from, date_to)
    return (
        qs.order_by()
        .values(
            "day",
            "payment__order__currency",
            "payment__order__department",
            "payment__method",
        )
        .annotate(
            refund_cash=Coalesce(
                Sum("amount", filter=Q(method__in=REFUND_CASH_METHODS)),
                _ZERO,
                output_field=_MONEY,
            ),
            refund_cashless=Coalesce(
                Sum("amount", filter=Q(method__in=REFUND_CASHLESS_METHODS)),
                _ZERO,
                output_field=_MONEY,
            ),
            refunds=Count("id"),
        )
    )


def _computed_payment_status(total: Decimal, paid: Decimal) -> str:
    """Match the canonical payment-state formula without trusting stored drift."""
    if paid <= 0:
        return "unpaid"
    if total > 0 and paid >= total:
        return "settled"
    return "partial"


def _period_shipped_snapshots(orders_qs, date_from, date_to) -> list[dict]:
    """Load period shipments once and calculate their current financial split.

    Items and payments are prefetched in two bulk queries. Consequently using
    ``Order.total_amount``/``paid_total`` below never creates an N+1 and avoids
    the item x payment multiplication produced by a naive joined aggregate.
    """
    qs = (
        orders_qs.filter(status="shipped")
        .annotate(
            day=TruncDate(Coalesce("shipment__shipped_at", "created_at"))
        )
        .select_related("client__user")
        .prefetch_related("items", "payments")
    )
    qs = _day_bounds(qs, date_from, date_to)

    result = []
    for order in qs:
        total = max(_ZERO, order.total_amount)
        paid = max(_ZERO, order.paid_total)
        allocated_paid = min(paid, total)
        remaining = max(_ZERO, total - paid)
        is_debt = order.settlement_intent == "debt" and remaining > 0
        debt = remaining if is_debt else _ZERO
        awaiting = remaining - debt
        result.append({
            "order": order,
            "day": order.day,
            "currency": order.currency or DEFAULT_CURRENCY,
            "bags": sum(item.quantity for item in order.items.all()),
            "total": total,
            "paid_amount": allocated_paid,
            "remaining_amount": remaining,
            "debt_amount": debt,
            "awaiting_amount": awaiting,
            "is_debt": is_debt,
            "payment_status": _computed_payment_status(total, paid),
        })
    return result


def _clients_breakdown(snapshots: list[dict]):
    """Current paid/debt/awaiting split for shipments in the selected period."""
    clients: dict = {}
    for snapshot in snapshots:
        order = snapshot["order"]
        currency = snapshot["currency"]
        entry = clients.setdefault(order.client_id, {
            "id": order.client_id,
            "name": order.client.name,
            "orders": 0,
            "bags": 0,
            "revenue_by_currency": defaultdict(lambda: _ZERO),
            "paid_amount_by_currency": defaultdict(lambda: _ZERO),
            "debt_amount_by_currency": defaultdict(lambda: _ZERO),
            "awaiting_amount_by_currency": defaultdict(lambda: _ZERO),
            "order_list": [],
        })
        entry["orders"] += 1
        entry["bags"] += snapshot["bags"]
        entry["revenue_by_currency"][currency] += snapshot["total"]
        entry["paid_amount_by_currency"][currency] += snapshot["paid_amount"]
        entry["debt_amount_by_currency"][currency] += snapshot["debt_amount"]
        entry["awaiting_amount_by_currency"][currency] += snapshot["awaiting_amount"]
        entry["order_list"].append({
            "id": order.id,
            "date": snapshot["day"].isoformat(),
            "bags": snapshot["bags"],
            "total": _d(snapshot["total"]),
            "paid_amount": _d(snapshot["paid_amount"]),
            "remaining_amount": _d(snapshot["remaining_amount"]),
            "currency": currency,
            "is_debt": snapshot["is_debt"],
            # Backward-compatible alias; unlike the old field it is a current
            # state, not a copy of settlement_intent.
            "on_debt": snapshot["is_debt"],
            "payment_status": snapshot["payment_status"],
        })

    def revenue_key(entry):
        return max(entry["revenue_by_currency"].values(), default=_ZERO)

    result = sorted(clients.values(), key=revenue_key, reverse=True)
    for entry in result:
        entry["order_list"].sort(
            key=lambda order: (order["date"], order["id"]),
            reverse=True,
        )
        currency = primary_currency(entry["revenue_by_currency"])
        entry["currency"] = currency
        for field in (
            "revenue", "paid_amount", "debt_amount", "awaiting_amount",
        ):
            source = (
                "revenue_by_currency"
                if field == "revenue"
                else f"{field}_by_currency"
            )
            entry[field] = _d(entry[source].get(currency, _ZERO))
            entry[source] = as_money_strings(entry[source])
    return result


def _debt_now(orders_qs):
    """Снапшот дебиторки на сейчас — по правилам orders/debt.py."""
    orders = list(
        orders_qs.filter(status="shipped", settlement_intent="debt")
        .prefetch_related("items", "payments")
    )
    outstanding = debt_orders(orders)
    totals = sum_by_currency(outstanding, order_remaining)
    currency = primary_currency(totals)

    from apps.clients.models import Store
    from apps.clients.services import is_payment_window_open

    store_ids = {order.store_id for order in outstanding if order.store_id}
    overdue_store_ids = {
        store.id
        for store in Store.objects.filter(id__in=store_ids)
        if store.payment_schedule_type != "none"
        and is_payment_window_open(store, timezone.localdate())
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
    return {
        "revenue": _ZERO,
        "paid_amount": _ZERO,
        "debt_amount": _ZERO,
        "awaiting_amount": _ZERO,
    }


def _income_currency_row():
    return {
        "gross_cash": _ZERO,
        "gross_cashless": _ZERO,
        "refund_cash": _ZERO,
        "refund_cashless": _ZERO,
    }


def summary_report(orders_qs, date_from=None, date_to=None, *, income_only=False) -> dict:
    """Собрать отчёт по живым заказам скоупа."""
    days: dict = {}
    departments = {}

    def department_row(code):
        return departments.setdefault(
            code,
            {
                "code": code,
                "orders": 0,
                "payments": 0,
                "sales_by_currency": defaultdict(lambda: _ZERO),
                "received_by_currency": defaultdict(lambda: _ZERO),
                "refunded_by_currency": defaultdict(lambda: _ZERO),
            },
        )

    def day_row(day):
        return days.setdefault(day, {
            "date": day.isoformat(),
            "orders": 0,
            "bags": 0,
            "payments": 0,
            "refunds": 0,
            "revenue_by_currency": defaultdict(lambda: _ZERO),
            "paid_amount_by_currency": defaultdict(lambda: _ZERO),
            "debt_amount_by_currency": defaultdict(lambda: _ZERO),
            "awaiting_amount_by_currency": defaultdict(lambda: _ZERO),
            "gross_cash_by_currency": defaultdict(lambda: _ZERO),
            "gross_cashless_by_currency": defaultdict(lambda: _ZERO),
            "refund_cash_by_currency": defaultdict(lambda: _ZERO),
            "refund_cashless_by_currency": defaultdict(lambda: _ZERO),
        })

    shipped_by_currency = defaultdict(_shipping_currency_row)
    snapshots = [] if income_only else _period_shipped_snapshots(orders_qs, date_from, date_to)
    total_bags = 0
    for snapshot in snapshots:
        department = department_row(snapshot["order"].department)
        department["orders"] += 1
        department["sales_by_currency"][snapshot["currency"]] += snapshot["total"]
        row = day_row(snapshot["day"])
        currency = snapshot["currency"]
        row["orders"] += 1
        row["bags"] += snapshot["bags"]
        total_bags += snapshot["bags"]
        for field in (
            "revenue", "paid_amount", "debt_amount", "awaiting_amount",
        ):
            value = snapshot["total"] if field == "revenue" else snapshot[field]
            row[f"{field}_by_currency"][currency] += value
            shipped_by_currency[currency][field] += value

    income_by_currency = defaultdict(_income_currency_row)
    # {валюта: {способ: нетто}} — возврат вычитается из способа исходной
    # оплаты, как paid_by_method в транзакциях; нал/безнал считаются как раньше.
    income_by_method = defaultdict(lambda: defaultdict(lambda: _ZERO))
    payments_by_method = defaultdict(int)
    payments_total = 0
    for event in _payment_events_by_day(orders_qs, date_from, date_to):
        row = day_row(event["day"])
        currency = event["order__currency"] or DEFAULT_CURRENCY
        gross = event["gross_cash"] + event["gross_cashless"]
        department = department_row(event["order__department"])
        department["received_by_currency"][currency] += gross
        department["payments"] += event["payments"]
        row["gross_cash_by_currency"][currency] += event["gross_cash"]
        row["gross_cashless_by_currency"][currency] += event["gross_cashless"]
        row["payments"] += event["payments"]
        income_by_currency[currency]["gross_cash"] += event["gross_cash"]
        income_by_currency[currency]["gross_cashless"] += event["gross_cashless"]
        income_by_method[currency][event["method"]] += gross
        payments_by_method[event["method"]] += event["payments"]
        payments_total += event["payments"]

    refunds_total = 0
    for event in _refund_events_by_day(orders_qs, date_from, date_to):
        row = day_row(event["day"])
        currency = event["payment__order__currency"] or DEFAULT_CURRENCY
        department_row(event["payment__order__department"])["refunded_by_currency"][
            currency
        ] += (event["refund_cash"] + event["refund_cashless"])
        row["refund_cash_by_currency"][currency] += event["refund_cash"]
        row["refund_cashless_by_currency"][currency] += event["refund_cashless"]
        row["refunds"] += event["refunds"]
        income_by_currency[currency]["refund_cash"] += event["refund_cash"]
        income_by_currency[currency]["refund_cashless"] += event["refund_cashless"]
        income_by_method[currency][event["payment__method"]] -= (
            event["refund_cash"] + event["refund_cashless"]
        )
        refunds_total += event["refunds"]

    revenue_by_currency = {
        currency: values["revenue"]
        for currency, values in shipped_by_currency.items()
    }
    revenue_currency = primary_currency(revenue_by_currency)
    revenue_totals = shipped_by_currency.get(
        revenue_currency, _shipping_currency_row()
    )

    gross_by_currency = {
        currency: values["gross_cash"] + values["gross_cashless"]
        for currency, values in income_by_currency.items()
    }
    refunded_by_currency = {
        currency: values["refund_cash"] + values["refund_cashless"]
        for currency, values in income_by_currency.items()
    }
    net_by_currency = {
        currency: gross_by_currency[currency] - refunded_by_currency[currency]
        for currency in income_by_currency
    }
    income_activity = {
        currency: gross_by_currency[currency] + refunded_by_currency[currency]
        for currency in income_by_currency
    }
    income_currency = primary_currency(income_activity)
    income_totals = income_by_currency.get(
        income_currency, _income_currency_row()
    )
    income_cash = income_totals["gross_cash"] - income_totals["refund_cash"]
    income_cashless = (
        income_totals["gross_cashless"] - income_totals["refund_cashless"]
    )

    income = {
        "total": _d(income_cash + income_cashless),
        "gross": _d(
            income_totals["gross_cash"] + income_totals["gross_cashless"]
        ),
        "refunded": _d(
            income_totals["refund_cash"] + income_totals["refund_cashless"]
        ),
        "cash": _d(income_cash),
        "cashless": _d(income_cashless),
        "payments": payments_total,
        "refunds": refunds_total,
        "currency": income_currency,
        "by_currency": as_money_strings(net_by_currency),
        "gross_by_currency": as_money_strings(gross_by_currency),
        "refunded_by_currency": as_money_strings(refunded_by_currency),
        "cash_by_currency": as_money_strings({
            currency: values["gross_cash"] - values["refund_cash"]
            for currency, values in income_by_currency.items()
        }),
        "cashless_by_currency": as_money_strings({
            currency: values["gross_cashless"] - values["refund_cashless"]
            for currency, values in income_by_currency.items()
        }),
        "by_method_by_currency": {
            currency: as_money_strings(dict(methods))
            for currency, methods in income_by_method.items()
        },
        "payments_by_method": dict(payments_by_method),
    }
    # Reuse the same shipment/payment/refund rows as the total report. Separate
    # grouping keys avoid multiplying items by payments and add no per-department queries.
    labels = {row.code: row for row in Department.objects.all()} if departments else {}
    department_rows = []
    for code, values in sorted(departments.items()):
        label = labels.get(code)
        received = values["received_by_currency"]
        refunded = values["refunded_by_currency"]
        department_rows.append(
            {
                "code": code,
                "name": label.name if label else (code or "Нет отдела"),
                "color": label.color if label else "#64748B",
                "orders": values["orders"] if not income_only else None,
                "payments": values["payments"],
                "sales_by_currency": (
                    as_money_strings(values["sales_by_currency"])
                    if not income_only
                    else None
                ),
                "received_by_currency": as_money_strings(received),
                "refunded_by_currency": as_money_strings(refunded),
                "net_by_currency": as_money_strings(
                    {
                        currency: received.get(currency, _ZERO)
                        - refunded.get(currency, _ZERO)
                        for currency in received.keys() | refunded.keys()
                    }
                ),
            }
        )
    if income_only:
        return {
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
            "income": income,
            "departments": department_rows,
        }

    day_list = []
    for row in sorted(days.values(), key=lambda value: value["date"], reverse=True):
        income_currencies = {
            *row["gross_cash_by_currency"],
            *row["gross_cashless_by_currency"],
            *row["refund_cash_by_currency"],
            *row["refund_cashless_by_currency"],
        }
        cash_by_currency = {
            currency: (
                row["gross_cash_by_currency"].get(currency, _ZERO)
                - row["refund_cash_by_currency"].get(currency, _ZERO)
            )
            for currency in income_currencies
        }
        cashless_by_currency = {
            currency: (
                row["gross_cashless_by_currency"].get(currency, _ZERO)
                - row["refund_cashless_by_currency"].get(currency, _ZERO)
            )
            for currency in income_currencies
        }
        gross_received_by_currency = {
            currency: (
                row["gross_cash_by_currency"].get(currency, _ZERO)
                + row["gross_cashless_by_currency"].get(currency, _ZERO)
            )
            for currency in income_currencies
        }
        day_refunded_by_currency = {
            currency: (
                row["refund_cash_by_currency"].get(currency, _ZERO)
                + row["refund_cashless_by_currency"].get(currency, _ZERO)
            )
            for currency in income_currencies
        }
        received_by_currency = {
            currency: (
                cash_by_currency[currency] + cashless_by_currency[currency]
            )
            for currency in income_currencies
        }
        day_list.append({
            "date": row["date"],
            "orders": row["orders"],
            "bags": row["bags"],
            "revenue": _d(row["revenue_by_currency"].get(revenue_currency, _ZERO)),
            "paid_amount": _d(
                row["paid_amount_by_currency"].get(revenue_currency, _ZERO)
            ),
            "debt_amount": _d(
                row["debt_amount_by_currency"].get(revenue_currency, _ZERO)
            ),
            "awaiting_amount": _d(
                row["awaiting_amount_by_currency"].get(revenue_currency, _ZERO)
            ),
            "cash": _d(cash_by_currency.get(income_currency, _ZERO)),
            "cashless": _d(cashless_by_currency.get(income_currency, _ZERO)),
            "received": _d(received_by_currency.get(income_currency, _ZERO)),
            "gross_received": _d(
                gross_received_by_currency.get(income_currency, _ZERO)
            ),
            "refunded": _d(
                day_refunded_by_currency.get(income_currency, _ZERO)
            ),
            "payments": row["payments"],
            "refunds": row["refunds"],
            "revenue_by_currency": as_money_strings(row["revenue_by_currency"]),
            "paid_amount_by_currency": as_money_strings(
                row["paid_amount_by_currency"]
            ),
            "debt_amount_by_currency": as_money_strings(
                row["debt_amount_by_currency"]
            ),
            "awaiting_amount_by_currency": as_money_strings(
                row["awaiting_amount_by_currency"]
            ),
            "cash_by_currency": as_money_strings(cash_by_currency),
            "cashless_by_currency": as_money_strings(cashless_by_currency),
            "received_by_currency": as_money_strings(received_by_currency),
            "gross_received_by_currency": as_money_strings(
                gross_received_by_currency
            ),
            "refunded_by_currency": as_money_strings(
                day_refunded_by_currency
            ),
        })

    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "income": income,
        "departments": department_rows,
        "shipped": {
            "revenue": _d(revenue_totals["revenue"]),
            "paid_amount": _d(revenue_totals["paid_amount"]),
            "debt_amount": _d(revenue_totals["debt_amount"]),
            "awaiting_amount": _d(revenue_totals["awaiting_amount"]),
            "orders": len(snapshots),
            "bags": total_bags,
            "currency": revenue_currency,
            "revenue_by_currency": as_money_strings(revenue_by_currency),
            "paid_amount_by_currency": as_money_strings(
                {
                    currency: values["paid_amount"]
                    for currency, values in shipped_by_currency.items()
                }
            ),
            "debt_amount_by_currency": as_money_strings(
                {
                    currency: values["debt_amount"]
                    for currency, values in shipped_by_currency.items()
                }
            ),
            "awaiting_amount_by_currency": as_money_strings(
                {
                    currency: values["awaiting_amount"]
                    for currency, values in shipped_by_currency.items()
                }
            ),
        },
        "debt_now": _debt_now(orders_qs),
        "clients": _clients_breakdown(snapshots),
        "days": day_list,
    }
