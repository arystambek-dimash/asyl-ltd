from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, TypedDict

from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.orders.models import Order, Payment, PaymentRefund
from apps.sales.models import Department

from ...models import Client
from .sections import ALL_CLIENT_SECTIONS, CLIENT_SECTIONS, select_sections

BASE_CURRENCIES = ("KZT", "USD")
PAYMENT_STAMP = Coalesce("confirmed_at", "paid_at")
REFUND_STAMP = Coalesce("completed_at", "updated_at", "created_at")
SALE_STAMP = Coalesce("shipment__shipped_at", "created_at")
ZERO = Decimal(0)


class CurrencyTotals(TypedDict):
    orders: int
    sales: Decimal
    payments: Decimal
    debt: Decimal


def empty_currency_totals() -> CurrencyTotals:
    return {
        "orders": 0,
        "sales": ZERO,
        "payments": ZERO,
        "debt": ZERO,
    }


@dataclass(frozen=True, slots=True)
class StatementOperation:
    occurred_at: datetime
    kind: Literal["sale", "payment", "refund"]
    order: Order
    payment: Payment | None
    refund: PaymentRefund | None
    amount: Decimal


@dataclass(slots=True)
class StatementData:
    client: Client | None
    clients: list[Client]
    orders: list[Order]
    sales_orders: list[Order]
    debt_orders: list[Order]
    payments: list[Payment]
    refunds: list[PaymentRefund]
    operations: list[StatementOperation]
    opening: dict[str, Decimal]
    client_opening: dict[tuple[int, str], Decimal]
    totals: dict[str, CurrencyTotals]
    client_totals: dict[tuple[int, str], CurrencyTotals]
    department_totals: dict[tuple[str, str], CurrencyTotals]
    currencies: tuple[str, ...]
    department_names: dict[str, str]
    department_scope: str
    department_codes: tuple[str, ...]
    period: str
    subtitle: str
    sections: tuple[str, ...]


def local_time(value):
    return timezone.localtime(value).replace(tzinfo=None) if value else None


def department_name(data: StatementData, code: str) -> str:
    return data.department_names.get(code, code)


def _payments_in_period(queryset, date_from, date_to):
    queryset = queryset.annotate(_stamp=PAYMENT_STAMP)
    if date_from:
        queryset = queryset.filter(_stamp__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(_stamp__date__lte=date_to)
    return queryset.order_by("_stamp", "id")


def _refunds_in_period(queryset, date_from, date_to):
    queryset = queryset.annotate(_stamp=REFUND_STAMP)
    if date_from:
        queryset = queryset.filter(_stamp__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(_stamp__date__lte=date_to)
    return queryset.order_by("_stamp", "id")


def _orders_in_period(queryset, date_from, date_to, *, sale_date=False):
    if sale_date:
        queryset = queryset.annotate(_statement_stamp=SALE_STAMP)
        field = "_statement_stamp__date"
    else:
        field = "created_at__date"
    if date_from:
        queryset = queryset.filter(**{f"{field}__gte": date_from})
    if date_to:
        queryset = queryset.filter(**{f"{field}__lte": date_to})
    return queryset.order_by(field.removesuffix("__date"), "id")


def _statement_orders(client=None, departments=None, client_ids=None):
    queryset = (
        Order
        .objects
        .select_related(
            "client__user",
            "store",
            "shipment",
            "repeated_from",
            "created_by",
        )
        .prefetch_related(
            "items__product",
            "payments__recorded_by",
            "payments__received_by",
            "payments__confirmed_by",
        )
    )
    if client is not None:
        queryset = queryset.filter(client=client)
    if client_ids is not None:
        queryset = queryset.filter(client_id__in=client_ids)
    if departments is not None:
        queryset = queryset.filter(department__in=departments)
    return queryset


def _statement_payments(client=None, departments=None, client_ids=None):
    queryset = (
        Payment.objects.filter(order__deleted_at__isnull=True)
        # Legacy service rows record debt classification, not received money.
        .exclude(method="debt")
        .select_related(
            "order__client__user",
            "recorded_by",
            "received_by",
            "confirmed_by",
        )
    )
    if client is not None:
        queryset = queryset.filter(order__client=client)
    if client_ids is not None:
        queryset = queryset.filter(order__client_id__in=client_ids)
    if departments is not None:
        queryset = queryset.filter(order__department__in=departments)
    return queryset


def _statement_refunds(client=None, departments=None, client_ids=None):
    # Refunds have their own recognition date. Filtering through Payment would
    # move a later refund back to the original confirmation period. As with the
    # payment queryset, the explicit deleted-order predicate is required because
    # traversing ``payment__order`` does not apply Order's live manager.
    queryset = PaymentRefund.objects.filter(
        status="completed",
        payment__order__deleted_at__isnull=True,
    ).exclude(payment__method="debt").select_related(
        "payment__order__client__user",
        "payment__recorded_by",
        "payment__received_by",
        "payment__confirmed_by",
        "requested_by",
    )
    if client is not None:
        queryset = queryset.filter(payment__order__client=client)
    if client_ids is not None:
        queryset = queryset.filter(payment__order__client_id__in=client_ids)
    if departments is not None:
        queryset = queryset.filter(payment__order__department__in=departments)
    return queryset


def _current_debt_orders(queryset):
    return [
        order
        for order in queryset.filter(
            status="shipped",
            settlement_intent="debt",
        ).order_by("created_at", "id")
        if order.is_debt
    ]


def _client_opening_balances(
        orders_queryset, payments_queryset, refunds_queryset, date_from,
):
    balances: defaultdict[tuple[int, str], Decimal] = defaultdict(Decimal)
    if not date_from:
        return balances
    for order in (
            orders_queryset
                    .filter(status="shipped")
                    .annotate(_statement_stamp=SALE_STAMP)
                    .filter(_statement_stamp__date__lt=date_from)
    ):
        balances[(order.client_id, order.currency)] += order.total_amount
    for payment in (
            payments_queryset.annotate(
                _stamp=PAYMENT_STAMP
            )
                    .filter(
                _stamp__date__lt=date_from, status="confirmed"
            )
    ):
        key = (payment.order.client_id, payment.order.currency)
        # Recognition is event based: the gross receipt belongs to the payment
        # confirmation day; completed refunds are applied on their own day.
        balances[key] -= payment.amount
    for refund in (
            refunds_queryset.annotate(
                _stamp=REFUND_STAMP
            )
                    .filter(_stamp__date__lt=date_from)
    ):
        order = refund.payment.order
        balances[(order.client_id, order.currency)] += refund.amount
    return balances


def _ledger_currencies(*sources) -> tuple[str, ...]:
    seen: set[str] = set()
    for source in sources:
        seen.update(currency for currency, value in source.items() if value)
    return (*BASE_CURRENCIES, *sorted(seen - set(BASE_CURRENCIES)))


def _period_label(date_from, date_to):
    if not date_from and not date_to:
        return "за всё время"
    return (
        f"{date_from.strftime('%d.%m.%Y') if date_from else 'начала'} — "
        f"{date_to.strftime('%d.%m.%Y') if date_to else 'сегодня'}"
    )


def _department_context(departments):
    rows = list(Department.objects.all())
    names = {row.code: row.name for row in rows}
    if departments is None:
        return names, "Все отделы", tuple(names)
    selected = [names.get(code, code) for code in departments]
    return names, ", ".join(selected), tuple(departments)


def _sale_stamp(order):
    shipment = getattr(order, "shipment", None)
    return getattr(shipment, "shipped_at", None) or order.created_at


def _payment_stamp(payment):
    return payment.confirmed_at or payment.paid_at


def _refund_stamp(refund: PaymentRefund):
    # completed_at is canonical. The fallback keeps migrated/legacy completed
    # rows visible instead of silently dropping money from a statement.
    return refund.completed_at or refund.updated_at or refund.created_at


def _operations(sales_orders, payments, refunds) -> list[StatementOperation]:
    operations = [
        StatementOperation(
            occurred_at=_sale_stamp(order),
            kind="sale",
            order=order,
            payment=None,
            refund=None,
            amount=order.total_amount,
        )
        for order in sales_orders
    ]
    operations += [
        StatementOperation(
            occurred_at=_payment_stamp(payment),
            kind="payment",
            order=payment.order,
            payment=payment,
            refund=None,
            amount=-payment.amount,
        )
        for payment in payments
        if payment.status == "confirmed"
    ]
    operations += [
        StatementOperation(
            occurred_at=_refund_stamp(refund),
            kind="refund",
            order=refund.payment.order,
            payment=refund.payment,
            refund=refund,
            # The ledger balance is the client's debt: a cash outflow reopens
            # the receivable and is therefore a positive balance movement.
            amount=refund.amount,
        )
        for refund in refunds
    ]
    kind_order = {"sale": 0, "payment": 1, "refund": 2}
    operations.sort(
        key=lambda operation: (
            operation.occurred_at,
            kind_order[operation.kind],
            operation.refund.id
            if operation.refund is not None
            else operation.payment.id
            if operation.payment is not None
            else operation.order.id,
        )
    )
    return operations


def _clients_for_statement(
        client,
        departments,
        date_from,
        date_to,
        orders,
        sales_orders,
        debt_orders,
        payments,
        refunds,
        client_opening,
        client_ids,
):
    if client is not None:
        return [client]
    queryset = Client.objects.all()
    if client_ids is not None:
        queryset = queryset.filter(id__in=client_ids)
    if departments is not None or date_from or date_to:
        relevant_ids = {
            *(order.client_id for order in orders),
            *(order.client_id for order in sales_orders),
            *(order.client_id for order in debt_orders),
            *(payment.order.client_id for payment in payments),
            *(refund.payment.order.client_id for refund in refunds),
            *(
                client_id
                for (client_id, _currency), balance in client_opening.items()
                if balance
            ),
        }
        queryset = queryset.filter(id__in=relevant_ids)
    return list(
        queryset.select_related("user").order_by(
            "user__first_name",
            "user__last_name",
            "id",
        )
    )


def build_statement_data(
        *,
        client=None,
        date_from=None,
        date_to=None,
        departments=None,
        sections=None,
        client_ids=None,
) -> StatementData:
    base_orders = _statement_orders(
        client=client,
        departments=departments,
        client_ids=client_ids,
    )
    payments_queryset = _statement_payments(
        client=client,
        departments=departments,
        client_ids=client_ids,
    )
    refunds_queryset = _statement_refunds(
        client=client,
        departments=departments,
        client_ids=client_ids,
    )
    orders = list(_orders_in_period(base_orders, date_from, date_to))
    sales_orders = list(
        _orders_in_period(
            base_orders
            .filter(status="shipped"),
            date_from,
            date_to,
            sale_date=True,
        )
    )
    debt_orders = _current_debt_orders(base_orders)
    payments = list(
        _payments_in_period(payments_queryset, date_from, date_to)
    )
    refunds = list(
        _refunds_in_period(refunds_queryset, date_from, date_to)
    )
    client_opening = _client_opening_balances(
        base_orders,
        payments_queryset,
        refunds_queryset,
        date_from,
    )
    clients = _clients_for_statement(
        client,
        departments,
        date_from,
        date_to,
        orders,
        sales_orders,
        debt_orders,
        payments,
        refunds,
        client_opening,
        client_ids,
    )

    totals: defaultdict[str, CurrencyTotals] = defaultdict(
        empty_currency_totals
    )
    client_totals: defaultdict[tuple[int, str], CurrencyTotals] = defaultdict(
        empty_currency_totals
    )
    department_totals: defaultdict[
        tuple[str, str], CurrencyTotals
    ] = defaultdict(empty_currency_totals)

    for order in orders:
        for target in (
                totals[order.currency],
                client_totals[(order.client_id, order.currency)],
                department_totals[(order.department, order.currency)],
        ):
            target["orders"] += 1
    for order in sales_orders:
        for target in (
                totals[order.currency],
                client_totals[(order.client_id, order.currency)],
                department_totals[(order.department, order.currency)],
        ):
            target["sales"] += order.total_amount
    for order in debt_orders:
        remaining = max(ZERO, order.remaining_amount)
        for target in (
                totals[order.currency],
                client_totals[(order.client_id, order.currency)],
                department_totals[(order.department, order.currency)],
        ):
            target["debt"] += remaining
    for payment in payments:
        if payment.status != "confirmed":
            continue
        order = payment.order
        for target in (
                totals[order.currency],
                client_totals[(order.client_id, order.currency)],
                department_totals[(order.department, order.currency)],
        ):
            target["payments"] += payment.amount
    for refund in refunds:
        order = refund.payment.order
        for target in (
                totals[order.currency],
                client_totals[(order.client_id, order.currency)],
                department_totals[(order.department, order.currency)],
        ):
            # ``payments`` remains the export's net received-money column.
            # A completed refund is a negative receipt in its completion period.
            target["payments"] -= refund.amount

    opening: defaultdict[str, Decimal] = defaultdict(Decimal)

    for (_, currency), value in client_opening.items():
        opening[currency] += value

    currencies = _ledger_currencies(
        opening,
        {code: value["sales"] for code, value in totals.items()},
        {code: value["payments"] for code, value in totals.items()},
        {code: value["debt"] for code, value in totals.items()},
    )
    for currency in currencies:
        totals[currency]
        for row_client in clients:
            client_totals[(row_client.id, currency)]

    department_names, department_scope, department_codes = (
        _department_context(departments)
    )
    period = _period_label(date_from, date_to)
    generated_at = timezone.localtime()
    prefix = f"{client.name} · " if client is not None else ""
    subtitle = (
        f"{prefix}{department_scope} · {period} · "
        f"сформировано {generated_at:%d.%m.%Y %H:%M}"
    )
    available_sections = (
        CLIENT_SECTIONS if client is not None else ALL_CLIENT_SECTIONS
    )

    return StatementData(
        client=client,
        clients=clients,
        orders=orders,
        sales_orders=sales_orders,
        debt_orders=debt_orders,
        payments=payments,
        refunds=refunds,
        operations=_operations(sales_orders, payments, refunds),
        opening=opening,
        client_opening=client_opening,
        totals=totals,
        client_totals=client_totals,
        department_totals=department_totals,
        currencies=currencies,
        department_names=department_names,
        department_scope=department_scope,
        department_codes=department_codes,
        period=period,
        subtitle=subtitle,
        sections=select_sections(sections, available_sections),
    )
