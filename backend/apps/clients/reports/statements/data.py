from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Literal, TypedDict

from django.db.models import F
from django.utils import timezone

from apps.common.money import CURRENCY_CODES, ZERO
from apps.common.query_params import filter_date_range
from apps.orders.debt import DEBT_STATUS, debt_orders as current_debt_orders, order_remaining
from apps.orders.models import Order, Payment, PaymentRefund
from apps.sales.labels import UNASSIGNED_NAME
from apps.sales.models import Department

from ...models import Client
from .sections import ALL_CLIENT_SECTIONS, CLIENT_SECTIONS, select_sections



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
    # Остаток клиента в валюте заказа после операции (от входящего остатка).
    # В выписке клиента это и есть остаток ленты.
    balance_after: Decimal = ZERO


@dataclass(slots=True)
class StatementData:
    client: Client | None
    clients: list[Client]
    orders: list[Order]
    debt_orders: list[Order]
    payments: list[Payment]
    refunds: list[PaymentRefund]
    operations: list[StatementOperation]
    opening: dict[str, Decimal]
    closing: dict[str, Decimal]
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
    return data.department_names.get(code, code or UNASSIGNED_NAME)


def _stamped(queryset, stamp, *, date_from=None, date_to=None, before=None):
    """Строки с днём ``stamp`` в периоде (или раньше ``before``), по хронологии."""
    queryset = filter_date_range(queryset.annotate(_stamp=stamp), "_stamp", date_from, date_to)
    if before:
        queryset = queryset.filter(_stamp__date__lt=before)
    return queryset.order_by("_stamp", "id")


def _statement_orders(client=None, departments=None, client_ids=None):
    queryset = (
        Order.objects
        .select_related(
            "client__user",
            "store",
            "shipment",
            "created_by",
        )
        .prefetch_related(
            "items__product",
            # Ячейка «Номер» вагонного заказа — вагоны отгрузки по отчёту.
            "shipment__wagons",
            "payments",
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
        .exclude(method__in=Payment.NON_MONEY_METHODS)
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
    # payment queryset, the explicit trash filter is required because
    # traversing ``payment__order`` does not apply Order's live manager.
    queryset = PaymentRefund.objects.filter(
        payment__order__deleted_at__isnull=True,
        status="completed",
    ).exclude(payment__method__in=Payment.NON_MONEY_METHODS).select_related(
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


def _client_opening_balances(
        orders_queryset, payments_queryset, refunds_queryset, date_from,
):
    balances: defaultdict[tuple[int, str], Decimal] = defaultdict(Decimal)
    if not date_from:
        return balances
    for order in _stamped(
            orders_queryset.filter(status="shipped"), Order.SALE_AT,
            before=date_from,
    ):
        balances[(order.client_id, order.currency)] += order.total_amount
    for payment in _stamped(
            payments_queryset.filter(status="confirmed"), Payment.RECOGNIZED_AT,
            before=date_from,
    ):
        key = (payment.order.client_id, payment.order.currency)
        # Recognition is event based: the gross receipt belongs to the payment
        # confirmation day; completed refunds are applied on their own day.
        balances[key] -= payment.amount
    for refund in _stamped(refunds_queryset, PaymentRefund.RECOGNIZED_AT, before=date_from):
        order = refund.payment.order
        balances[(order.client_id, order.currency)] += refund.amount
    return balances


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


def _operations(
        sales_orders, payments, refunds, client_opening,
) -> list[StatementOperation]:
    operations = [
        StatementOperation(
            occurred_at=order.sale_at,
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
            occurred_at=payment.recognized_at,
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
            occurred_at=refund.recognized_at,
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
    balances = defaultdict(Decimal, client_opening)
    with_balances = []
    for operation in operations:
        key = (operation.order.client_id, operation.order.currency)
        balances[key] += operation.amount
        with_balances.append(replace(operation, balance_after=balances[key]))
    return with_balances


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
    period = {"date_from": date_from, "date_to": date_to}
    # Информационный лист заказов — по дате создания, продажи — по отгрузке.
    orders = list(_stamped(base_orders, F("created_at"), **period))
    sales_orders = list(
        _stamped(base_orders.filter(status="shipped"), Order.SALE_AT, **period)
    )
    debt_orders = current_debt_orders(
        base_orders.filter(status=DEBT_STATUS).order_by("created_at", "id")
    )
    payments = list(_stamped(payments_queryset, Payment.RECOGNIZED_AT, **period))
    refunds = list(_stamped(refunds_queryset, PaymentRefund.RECOGNIZED_AT, **period))
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

    def add(order, field, amount):
        """Одна сумма заказа — в итог валюты, клиента и отдела сразу."""
        for target in (
                totals[order.currency],
                client_totals[(order.client_id, order.currency)],
                department_totals[(order.department, order.currency)],
        ):
            target[field] += amount

    for order in orders:
        add(order, "orders", 1)
    for order in sales_orders:
        add(order, "sales", order.total_amount)
    for order in debt_orders:
        add(order, "debt", order_remaining(order))
    for payment in payments:
        if payment.status == "confirmed":
            add(payment.order, "payments", payment.amount)
    for refund in refunds:
        # ``payments`` remains the export's net received-money column.
        # A completed refund is a negative receipt in its completion period.
        add(refund.payment.order, "payments", -refund.amount)

    opening: defaultdict[str, Decimal] = defaultdict(Decimal)

    for (_, currency), value in client_opening.items():
        opening[currency] += value

    # Every statement currency gets a row, even without movements.
    currencies = CURRENCY_CODES
    for currency in currencies:
        totals.setdefault(currency, empty_currency_totals())
        for row_client in clients:
            client_totals.setdefault(
                (row_client.id, currency), empty_currency_totals()
            )
    closing = {
        currency: (
            opening[currency]
            + totals[currency]["sales"]
            - totals[currency]["payments"]
        )
        for currency in currencies
    }

    department_names, department_scope, department_codes = (
        _department_context(departments)
    )
    period_label = _period_label(date_from, date_to)
    generated_at = timezone.localtime()
    prefix = f"{client.name} · " if client is not None else ""
    subtitle = (
        f"{prefix}{department_scope} · {period_label} · "
        f"сформировано {generated_at:%d.%m.%Y %H:%M}"
    )
    available_sections = (
        CLIENT_SECTIONS if client is not None else ALL_CLIENT_SECTIONS
    )

    return StatementData(
        client=client,
        clients=clients,
        orders=orders,
        debt_orders=debt_orders,
        payments=payments,
        refunds=refunds,
        operations=_operations(
            sales_orders, payments, refunds, client_opening
        ),
        opening=opening,
        closing=closing,
        client_opening=client_opening,
        totals=totals,
        client_totals=client_totals,
        department_totals=department_totals,
        currencies=currencies,
        department_names=department_names,
        department_scope=department_scope,
        department_codes=department_codes,
        period=period_label,
        subtitle=subtitle,
        sections=select_sections(sections, available_sections),
    )
