"""Excel-выписка клиента: заказы, продажи, оплаты и текущие долги."""
from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from typing import TypedDict

from django.db.models.functions import Coalesce
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.orders.labels import (
    order_payment_method_label, payment_method_label, payment_status_label,
    transport_label,
)
from apps.orders.models import Order, Payment
from apps.orders.statuses import public_status_label


def _method_label(method: str) -> str:
    """В выписке отмечаем архивные способы, чтобы их не искали в кассе."""
    return payment_method_label(method, archived_hint=True)


NAVY = "17233B"
BLUE = "3367D6"
LIGHT_BLUE = "EAF1FF"
LIGHT_GRAY = "F4F6F9"
GREEN = "1F9D6A"
RED = "D94C3D"
WHITE = "FFFFFF"
THIN = Side(style="thin", color="D9E0EA")
FORMULA_PREFIXES = ("=", "+", "-", "@")


class _CurrencyTotals(TypedDict):
    orders: int
    sales: Decimal
    payments: Decimal
    debt: Decimal


def _empty_currency_totals() -> _CurrencyTotals:
    return {
        "orders": 0,
        "sales": Decimal("0"),
        "payments": Decimal("0"),
        "debt": Decimal("0"),
    }


def _local(value):
    return timezone.localtime(value).replace(tzinfo=None) if value else None


def _money(value):
    """Денежное значение для ячейки Excel.

    openpyxl пишет Decimal нативно, а float на суммах в миллионы тенге теряет
    копейки (99999999.99 хранится как 99999999.98999999…): выписка клиента
    начинала расходиться с API. Формат ячейки задаёт `_finish`.
    """
    return Decimal(value or 0)


# Оплата признаётся днём подтверждения кассой; если подтверждения ещё нет —
# днём записи. Тот же момент показывается в строках выписки, поэтому фильтр
# периода обязан считать по нему же, иначе деньги, подтверждённые в периоде,
# в выписку не попадут, а попавшие получат дату вне запрошенного окна.
PAYMENT_STAMP = Coalesce("confirmed_at", "paid_at")
SALE_STAMP = Coalesce("shipment__shipped_at", "created_at")


def _payments_in_period(queryset, date_from, date_to):
    queryset = queryset.annotate(_stamp=PAYMENT_STAMP)
    if date_from:
        queryset = queryset.filter(_stamp__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(_stamp__date__lte=date_to)
    return queryset.order_by("_stamp", "id")


def _orders_in_period(queryset, date_from, date_to, *, sale_date=False):
    """Filter by the date represented by a statement row.

    Orders/items use creation date. A recognized sale uses shipment time (with
    creation time only as a fallback for legacy manual shipments).
    """
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


def _period_label(date_from, date_to):
    if not date_from and not date_to:
        return "за всё время"
    return (
        f"{date_from.strftime('%d.%m.%Y') if date_from else 'начала'} — "
        f"{date_to.strftime('%d.%m.%Y') if date_to else 'сегодня'}"
    )


def _department_context(departments):
    from .models import Department

    rows = list(Department.objects.all())
    names = {row.code: row.name for row in rows}
    if departments is None:
        return names, "Все отделы"
    selected = [names.get(code, code) for code in departments]
    return names, ", ".join(selected)


def _department_name(names, code):
    return names.get(code, code)


def _statement_orders(client=None, departments=None):
    queryset = (
        Order.objects.select_related(
            "client", "store", "shipment", "repeated_from", "created_by",
        )
        .prefetch_related(
            "items__product", "payments__recorded_by", "payments__received_by",
            "payments__confirmed_by",
        )
    )
    if client is not None:
        queryset = queryset.filter(client=client)
    if departments is not None:
        queryset = queryset.filter(department__in=departments)
    return queryset


def _current_debt_orders(queryset):
    return [
        order
        for order in queryset.filter(
            status="shipped", settlement_intent="debt",
        ).order_by("created_at", "id")
        if order.is_debt
    ]


def _neutralize_formula_cells(workbook) -> None:
    """Keep exported user text literal in Excel-compatible applications.

    openpyxl treats a leading ``=`` as a formula, and spreadsheet applications
    may also execute strings beginning with ``+``, ``-`` or ``@``. Prefixing a
    quote is Excel's standard literal-text escape and does not alter numbers or
    dates used by report calculations and formatting.
    """
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                value = cell.value
                if not isinstance(value, str) or value.startswith("'"):
                    continue
                candidate = value.lstrip("\t\r\n")
                if candidate.startswith(FORMULA_PREFIXES):
                    cell.value = f"'{value}"


def _title(ws, title, subtitle, columns):
    ws.sheet_view.showGridLines = False
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    cell = ws.cell(1, 1, title)
    cell.font = Font(size=18, bold=True, color=WHITE)
    cell.fill = PatternFill("solid", fgColor=NAVY)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 34
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=columns)
    ws.cell(2, 1, subtitle).font = Font(size=10, color="6B7280")
    ws.row_dimensions[2].height = 24


def _headers(ws, row, values):
    for col, value in enumerate(values, 1):
        cell = ws.cell(row, col, value)
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.alignment = Alignment(vertical="center")
        cell.border = Border(bottom=THIN)
    ws.row_dimensions[row].height = 24
    ws.freeze_panes = f"A{row + 1}"
    ws.auto_filter.ref = f"A{row}:{get_column_letter(len(values))}{row}"


def _finish(ws, widths, money_columns=(), date_columns=()):
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for row in ws.iter_rows(min_row=4):
        for cell in row:
            cell.border = Border(bottom=THIN)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.row % 2 == 1:
                cell.fill = PatternFill("solid", fgColor="FAFBFD")
    for col in money_columns:
        for cell in ws[get_column_letter(col)][3:]:
            cell.number_format = '#,##0.00'
    for col in date_columns:
        for cell in ws[get_column_letter(col)][3:]:
            cell.number_format = "dd.mm.yyyy hh:mm"


def build_client_statement(
    client, date_from=None, date_to=None, departments=None,
) -> bytes:
    base_orders = _statement_orders(client=client, departments=departments)
    orders = list(_orders_in_period(base_orders, date_from, date_to))
    sales_orders = list(_orders_in_period(
        base_orders.filter(status="shipped"),
        date_from,
        date_to,
        sale_date=True,
    ))
    debt_orders = _current_debt_orders(base_orders)
    payments = Payment.objects.filter(
        order__client=client, order__deleted_at__isnull=True,
    ).select_related("order", "recorded_by", "received_by", "confirmed_by")
    if departments is not None:
        payments = payments.filter(order__department__in=departments)
    payments = list(_payments_in_period(payments, date_from, date_to))

    period = _period_label(date_from, date_to)
    department_names, department_scope = _department_context(departments)
    subtitle = (
        f"{client.name} · {department_scope} · {period} · "
        f"сформировано {timezone.localtime():%d.%m.%Y %H:%M}"
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:F1")
    ws["A1"] = "Выписка клиента"
    ws["A1"].font = Font(size=20, bold=True, color=WHITE)
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws.row_dimensions[1].height = 38
    ws.merge_cells("A2:F2")
    ws["A2"] = subtitle
    ws["A2"].font = Font(color="6B7280")
    ws.append([])
    ws.append(["Клиент", client.name, "Телефон", client.phone, "Страна", client.country])
    ws.append(["Период", period, "ИИН / БИН", client.iin or "—", "Банк", client.bank or "—"])
    for row in range(4, 6):
        for cell in ws[row]:
            cell.fill = PatternFill("solid", fgColor=LIGHT_GRAY)
            cell.border = Border(bottom=THIN)
            if cell.column % 2 == 1:
                cell.font = Font(bold=True, color="64748B")

    totals: defaultdict[str, _CurrencyTotals] = defaultdict(
        _empty_currency_totals
    )
    for order in orders:
        totals[order.currency]["orders"] += 1
    for order in sales_orders:
        totals[order.currency]["sales"] += order.total_amount
    for order in debt_orders:
        totals[order.currency]["debt"] += max(
            Decimal("0"), order.remaining_amount
        )
    for payment in payments:
        if payment.status == "confirmed":
            totals[payment.order.currency]["payments"] += payment.net_amount

    ws.append([])
    ws.append(["Валюта", "Заказов", "Продажи", "Оплачено", "Текущий долг", "Баланс выписки"])
    for cell in ws[7]:
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=BLUE)
    for currency in ("KZT", "USD"):
        currency_totals = totals[currency]
        ws.append([
            currency, currency_totals["orders"], _money(currency_totals["sales"]),
            _money(currency_totals["payments"]), _money(currency_totals["debt"]),
            _money(currency_totals["sales"] - currency_totals["payments"]),
        ])
    for row in ws.iter_rows(min_row=8, max_row=9):
        for cell in row:
            cell.border = Border(bottom=THIN)
        for cell in row[2:]:
            cell.number_format = '#,##0.00'
    for col, width in enumerate((16, 16, 20, 20, 20, 22), 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    # Kaspi-подобная лента: дебет = отгрузка, кредит = подтверждённая оплата.
    ledger = wb.create_sheet("Операции")
    _title(ledger, "Операции", subtitle, 11)
    _headers(ledger, 3, [
        "Дата", "Операция", "Заказ", "Описание", "Способ / статус",
        "Валюта", "Начислено", "Оплачено", "Баланс периода", "Автор", "Отдел",
    ])
    operations = []
    for order in sales_orders:
        stamp = getattr(getattr(order, "shipment", None), "shipped_at", None) or order.created_at
        operations.append((stamp, 0, order.currency, order.total_amount, Decimal("0"), order))
    for payment in payments:
        if payment.status == "confirmed":
            stamp = payment.confirmed_at or payment.paid_at
            operations.append((stamp, 1, payment.order.currency, Decimal("0"), payment.net_amount, payment))
    operations.sort(key=lambda item: (item[0], item[1], getattr(item[5], "id", 0)))
    balances: defaultdict[str, Decimal] = defaultdict(Decimal)
    for stamp, kind, currency, debit, credit, obj in operations:
        balances[currency] += debit - credit
        if kind == 0:
            order = obj
            description = ", ".join(
                f"{item.product_label} × {item.quantity}" for item in order.items.all()
            )
            values = [
                _local(stamp), "Продажа / отгрузка", order.id, description,
                public_status_label(order.status), currency, _money(debit), 0,
                _money(balances[currency]), order.created_by.username if order.created_by else "—",
                _department_name(department_names, order.department),
            ]
        else:
            payment = obj
            author = payment.confirmed_by or payment.received_by or payment.recorded_by
            values = [
                _local(stamp), "Оплата", payment.order_id, payment.note or "Поступление оплаты",
                _method_label(payment.method), currency, 0,
                _money(credit), _money(balances[currency]), author.username if author else "—",
                _department_name(department_names, payment.order.department),
            ]
        ledger.append(values)
    _finish(ledger, (19, 21, 10, 44, 22, 10, 16, 16, 16, 20, 22), (7, 8, 9), (1,))

    orders_ws = wb.create_sheet("Заказы")
    _title(orders_ws, "Заказы", subtitle, 14)
    _headers(orders_ws, 3, [
        "№", "Создан", "Статус", "Отгружен", "Отдел", "Магазин",
        "Транспорт", "Номер", "Валюта", "Сумма", "Оплачено", "Долг",
        "Повтор заказа", "Примечание",
    ])
    for order in orders:
        shipment = getattr(order, "shipment", None)
        orders_ws.append([
            order.id, _local(order.created_at), public_status_label(order.status),
            _local(shipment.shipped_at) if shipment else None,
            _department_name(department_names, order.department),
            order.store.name if order.store else "—",
            transport_label(order.transport_type),
            order.truck_number or "—", order.currency, _money(order.total_amount),
            _money(order.paid_total), _money(max(Decimal("0"), order.remaining_amount)),
            order.repeated_from_id, order.notes,
        ])
    _finish(orders_ws, (9, 19, 20, 19, 18, 22, 12, 16, 10, 16, 16, 16, 15, 35), (10, 11, 12), (2, 4))

    items_ws = wb.create_sheet("Позиции")
    _title(items_ws, "Позиции заказов", subtitle, 9)
    _headers(items_ws, 3, [
        "Заказ", "Дата", "Товар", "Класс CV", "Мешков", "Цена / мешок", "Сумма", "Валюта", "Отдел",
    ])
    for order in orders:
        for item in order.items.all():
            items_ws.append([
                order.id, _local(order.created_at), item.product_label,
                item.product_cv_class or "—", item.quantity,
                _money(item.unit_price), _money(item.quantity * (item.unit_price or 0)),
                order.currency, _department_name(department_names, order.department),
            ])
    _finish(items_ws, (10, 19, 40, 16, 12, 18, 18, 10, 22), (6, 7), (2,))

    pay_ws = wb.create_sheet("Платежи")
    _title(pay_ws, "Платежи", subtitle, 10)
    _headers(pay_ws, 3, [
        "№", "Дата", "Заказ", "Способ", "Статус", "Сумма", "Валюта", "Сотрудник", "Примечание", "Отдел",
    ])
    for payment in payments:
        author = payment.confirmed_by or payment.received_by or payment.recorded_by
        pay_ws.append([
            payment.id, _local(payment.confirmed_at or payment.paid_at), payment.order_id,
            _method_label(payment.method),
            payment_status_label(payment.status), _money(payment.amount),
            payment.order.currency, author.username if author else "—", payment.note,
            _department_name(department_names, payment.order.department),
        ])
    _finish(pay_ws, (9, 19, 10, 20, 18, 18, 10, 20, 38, 22), (6,), (2,))

    debt_ws = wb.create_sheet("Долги")
    _title(debt_ws, "Текущие долги", subtitle, 10)
    _headers(debt_ws, 3, [
        "Заказ", "Отгружен", "Магазин", "Мешков", "Сумма", "Оплачено", "Остаток", "Валюта", "Способ", "Отдел",
    ])
    for order in debt_orders:
        shipment = getattr(order, "shipment", None)
        debt_ws.append([
            order.id, _local(shipment.shipped_at) if shipment else _local(order.created_at),
            order.store.name if order.store else "—",
            sum(item.quantity for item in order.items.all()), _money(order.total_amount),
            _money(order.paid_total), _money(order.remaining_amount), order.currency,
            order_payment_method_label(order.payment_method),
            _department_name(department_names, order.department),
        ])
    _finish(debt_ws, (10, 19, 22, 12, 18, 18, 18, 10, 20, 22), (5, 6, 7), (2,))

    output = BytesIO()
    _neutralize_formula_cells(wb)
    wb.save(output)
    return output.getvalue()


def build_all_clients_statement(
    date_from=None, date_to=None, departments=None,
) -> bytes:
    """Консолидированная выписка по всей клиентской базе.

    Финансы разных валют намеренно не пересчитываются по курсу: KZT и USD
    остаются отдельными потоками, чтобы итог нельзя было неверно трактовать.
    """
    from .models import Client

    base_orders = _statement_orders(departments=departments)
    orders = list(_orders_in_period(base_orders, date_from, date_to))
    sales_orders = list(_orders_in_period(
        base_orders.filter(status="shipped"),
        date_from,
        date_to,
        sale_date=True,
    ))
    debt_orders = _current_debt_orders(base_orders)

    payments_qs = Payment.objects.filter(
        order__deleted_at__isnull=True,
    ).select_related("order", "order__client", "recorded_by", "received_by", "confirmed_by")
    if departments is not None:
        payments_qs = payments_qs.filter(order__department__in=departments)
    payments = list(_payments_in_period(payments_qs, date_from, date_to))

    period = _period_label(date_from, date_to)
    department_names, department_scope = _department_context(departments)
    relevant_client_ids = {
        *(order.client_id for order in orders),
        *(order.client_id for order in sales_orders),
        *(order.client_id for order in debt_orders),
        *(payment.order.client_id for payment in payments),
    }
    if departments is None and not date_from and not date_to:
        clients = list(Client.objects.order_by("first_name", "last_name", "id"))
    else:
        clients = list(Client.objects.filter(id__in=relevant_client_ids).order_by(
            "first_name", "last_name", "id"))
    subtitle = (
        f"{department_scope} · {period} · "
        f"сформировано {timezone.localtime():%d.%m.%Y %H:%M}"
    )

    totals: defaultdict[str, _CurrencyTotals] = defaultdict(
        _empty_currency_totals
    )
    client_totals: defaultdict[
        int, defaultdict[str, _CurrencyTotals]
    ] = defaultdict(lambda: defaultdict(_empty_currency_totals))
    department_totals: defaultdict[
        tuple[str, str], _CurrencyTotals
    ] = defaultdict(_empty_currency_totals)
    for order in orders:
        for target in (totals[order.currency], client_totals[order.client_id][order.currency]):
            target["orders"] += 1
        department_totals[(order.department, order.currency)]["orders"] += 1
    for order in sales_orders:
        for target in (totals[order.currency], client_totals[order.client_id][order.currency]):
            target["sales"] += order.total_amount
        department_totals[(order.department, order.currency)]["sales"] += order.total_amount
    for order in debt_orders:
        remaining = max(Decimal("0"), order.remaining_amount)
        for target in (totals[order.currency], client_totals[order.client_id][order.currency]):
            target["debt"] += remaining
        department_totals[(order.department, order.currency)]["debt"] += remaining
    for payment in payments:
        if payment.status != "confirmed":
            continue
        totals[payment.order.currency]["payments"] += payment.net_amount
        client_totals[payment.order.client_id][payment.order.currency]["payments"] += payment.net_amount
        department_totals[(payment.order.department, payment.order.currency)]["payments"] += payment.net_amount

    wb = Workbook()
    summary = wb.active
    summary.title = "Сводка"
    summary.sheet_view.showGridLines = False
    summary.merge_cells("A1:G1")
    summary["A1"] = "Общая выписка по клиентам"
    summary["A1"].font = Font(size=20, bold=True, color=WHITE)
    summary["A1"].fill = PatternFill("solid", fgColor=NAVY)
    summary.row_dimensions[1].height = 38
    summary.merge_cells("A2:G2")
    summary["A2"] = subtitle
    summary["A2"].font = Font(color="6B7280")
    summary.append([])
    summary.append(["Клиентов", len(clients), "Заказов", len(orders), "Платежей", len(payments), "Период"])
    summary.append(["Отделы", department_scope, "", "", "", "", period])
    for row in range(4, 6):
        for cell in summary[row]:
            cell.fill = PatternFill("solid", fgColor=LIGHT_GRAY)
            cell.border = Border(bottom=THIN)
            if (row == 4 and cell.column % 2 == 1) or (row == 5 and cell.column == 1):
                cell.font = Font(bold=True, color="64748B")
    summary.append([])
    summary.append([
        "Валюта", "Заказов", "Продажи", "Оплачено", "Текущий долг",
        "Баланс периода", "Клиентов с долгом",
    ])
    for cell in summary[7]:
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=BLUE)
    for currency in ("KZT", "USD"):
        currency_totals = totals[currency]
        clients_with_debt = sum(
            1 for client in clients
            if client_totals[client.id][currency]["debt"] > 0
        )
        summary.append([
            currency, currency_totals["orders"],
            _money(currency_totals["sales"]),
            _money(currency_totals["payments"]),
            _money(currency_totals["debt"]),
            _money(currency_totals["sales"] - currency_totals["payments"]),
            clients_with_debt,
        ])
    for row in summary.iter_rows(min_row=8, max_row=9):
        for cell in row:
            cell.border = Border(bottom=THIN)
        for cell in row[2:6]:
            cell.number_format = '#,##0.00'
    summary.append([])
    department_header_row = summary.max_row + 1
    summary.append([
        "Отдел", "Валюта", "Заказов", "Продажи", "Оплачено",
        "Текущий долг", "Баланс периода",
    ])
    for cell in summary[department_header_row]:
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=GREEN)
    department_codes = list(departments) if departments is not None else list(department_names)
    for code in department_codes:
        for currency in ("KZT", "USD"):
            row_totals = department_totals[(code, currency)]
            summary.append([
                _department_name(department_names, code),
                currency,
                row_totals["orders"],
                _money(row_totals["sales"]),
                _money(row_totals["payments"]),
                _money(row_totals["debt"]),
                _money(row_totals["sales"] - row_totals["payments"]),
            ])
    for row in summary.iter_rows(
        min_row=department_header_row + 1,
        max_row=summary.max_row,
    ):
        for cell in row:
            cell.border = Border(bottom=THIN)
        for cell in row[3:7]:
            cell.number_format = '#,##0.00'
    for col, width in enumerate((14, 14, 20, 20, 20, 20, 22), 1):
        summary.column_dimensions[get_column_letter(col)].width = width

    clients_ws = wb.create_sheet("Клиенты")
    _title(clients_ws, "Клиенты", subtitle, 15)
    _headers(clients_ws, 3, [
        "ID", "Клиент", "Компания", "Телефон", "ИИН / БИН", "Страна",
        "Валюта прайса", "Заказов KZT", "Продажи KZT", "Оплачено KZT",
        "Долг KZT", "Заказов USD", "Продажи USD", "Оплачено USD", "Долг USD",
    ])
    for client in clients:
        kzt = client_totals[client.id]["KZT"]
        usd = client_totals[client.id]["USD"]
        clients_ws.append([
            client.id, client.name, client.company_name or "—", client.phone,
            client.iin or "—", client.country or "—", client.currency,
            kzt["orders"], _money(kzt["sales"]), _money(kzt["payments"]), _money(kzt["debt"]),
            usd["orders"], _money(usd["sales"]), _money(usd["payments"]), _money(usd["debt"]),
        ])
    _finish(
        clients_ws,
        (8, 30, 28, 19, 16, 18, 15, 14, 18, 18, 18, 14, 18, 18, 18),
        (9, 10, 11, 13, 14, 15),
    )

    ledger = wb.create_sheet("Операции")
    _title(ledger, "Операции", subtitle, 13)
    _headers(ledger, 3, [
        "Дата", "Клиент", "Телефон", "Операция", "Заказ", "Описание",
        "Способ / статус", "Валюта", "Начислено", "Оплачено",
        "Баланс периода", "Автор", "Отдел",
    ])
    operations = []
    for order in sales_orders:
        stamp = getattr(getattr(order, "shipment", None), "shipped_at", None) or order.created_at
        operations.append((stamp, 0, order.client_id, order.currency, order.total_amount, Decimal("0"), order))
    for payment in payments:
        if payment.status == "confirmed":
            stamp = payment.confirmed_at or payment.paid_at
            operations.append((stamp, 1, payment.order.client_id, payment.order.currency, Decimal("0"), payment.net_amount, payment))
    operations.sort(key=lambda item: (item[0], item[1], getattr(item[6], "id", 0)))
    balances: defaultdict[tuple[int, str], Decimal] = defaultdict(Decimal)
    for stamp, kind, client_id, currency, debit, credit, obj in operations:
        balance_key = (client_id, currency)
        balances[balance_key] += debit - credit
        if kind == 0:
            order = obj
            description = ", ".join(
                f"{item.product_label} × {item.quantity}" for item in order.items.all()
            )
            values = [
                _local(stamp), order.client.name, order.client.phone,
                "Продажа / отгрузка", order.id, description,
                public_status_label(order.status), currency, _money(debit), 0,
                _money(balances[balance_key]),
                order.created_by.username if order.created_by else "—",
                _department_name(department_names, order.department),
            ]
        else:
            payment = obj
            author = payment.confirmed_by or payment.received_by or payment.recorded_by
            values = [
                _local(stamp), payment.order.client.name, payment.order.client.phone,
                "Оплата", payment.order_id, payment.note or "Поступление оплаты",
                _method_label(payment.method), currency, 0,
                _money(credit), _money(balances[balance_key]),
                author.username if author else "—",
                _department_name(department_names, payment.order.department),
            ]
        ledger.append(values)
    _finish(ledger, (19, 28, 18, 21, 10, 42, 22, 10, 16, 16, 18, 20, 22), (9, 10, 11), (1,))

    orders_ws = wb.create_sheet("Заказы")
    _title(orders_ws, "Все заказы", subtitle, 17)
    _headers(orders_ws, 3, [
        "№", "Создан", "Клиент", "Телефон", "Статус", "Отгружен", "Отдел",
        "Магазин", "Транспорт", "Номер", "Валюта", "Сумма", "Оплачено",
        "Долг", "Мешков", "Шаблон заказа", "Примечание",
    ])
    for order in orders:
        shipment = getattr(order, "shipment", None)
        orders_ws.append([
            order.id, _local(order.created_at), order.client.name, order.client.phone,
            public_status_label(order.status),
            _local(shipment.shipped_at) if shipment else None,
            _department_name(department_names, order.department),
            order.store.name if order.store else "—",
            transport_label(order.transport_type),
            order.truck_number or "—", order.currency, _money(order.total_amount),
            _money(order.paid_total), _money(max(Decimal("0"), order.remaining_amount)),
            sum(item.quantity for item in order.items.all()), order.repeated_from_id,
            order.notes,
        ])
    _finish(
        orders_ws,
        (9, 19, 30, 18, 20, 19, 18, 22, 12, 16, 10, 16, 16, 16, 12, 16, 35),
        (12, 13, 14), (2, 6),
    )

    items_ws = wb.create_sheet("Позиции")
    _title(items_ws, "Позиции всех заказов", subtitle, 11)
    _headers(items_ws, 3, [
        "Заказ", "Дата", "Клиент", "Телефон", "Товар", "Класс CV",
        "Мешков", "Цена / мешок", "Сумма", "Валюта", "Отдел",
    ])
    for order in orders:
        for item in order.items.all():
            items_ws.append([
                order.id, _local(order.created_at), order.client.name, order.client.phone,
                item.product_label, item.product_cv_class or "—", item.quantity,
                _money(item.unit_price), _money(item.quantity * (item.unit_price or 0)),
                order.currency, _department_name(department_names, order.department),
            ])
    _finish(items_ws, (10, 19, 30, 18, 40, 16, 12, 18, 18, 10, 22), (8, 9), (2,))

    pay_ws = wb.create_sheet("Платежи")
    _title(pay_ws, "Все платежи", subtitle, 12)
    _headers(pay_ws, 3, [
        "№", "Дата", "Клиент", "Телефон", "Заказ", "Способ", "Статус",
        "Сумма", "Валюта", "Сотрудник", "Примечание", "Отдел",
    ])
    for payment in payments:
        author = payment.confirmed_by or payment.received_by or payment.recorded_by
        pay_ws.append([
            payment.id, _local(payment.confirmed_at or payment.paid_at),
            payment.order.client.name, payment.order.client.phone, payment.order_id,
            _method_label(payment.method),
            payment_status_label(payment.status), _money(payment.amount),
            payment.order.currency, author.username if author else "—", payment.note,
            _department_name(department_names, payment.order.department),
        ])
    _finish(pay_ws, (9, 19, 30, 18, 10, 20, 18, 18, 10, 20, 38, 22), (8,), (2,))

    debt_ws = wb.create_sheet("Долги")
    _title(debt_ws, "Текущие долги", subtitle, 12)
    _headers(debt_ws, 3, [
        "Заказ", "Отгружен", "Клиент", "Телефон", "Магазин", "Мешков",
        "Сумма", "Оплачено", "Остаток", "Валюта", "Способ", "Отдел",
    ])
    for order in debt_orders:
        shipment = getattr(order, "shipment", None)
        debt_ws.append([
            order.id, _local(shipment.shipped_at) if shipment else _local(order.created_at),
            order.client.name, order.client.phone,
            order.store.name if order.store else "—",
            sum(item.quantity for item in order.items.all()), _money(order.total_amount),
            _money(order.paid_total), _money(order.remaining_amount), order.currency,
            order_payment_method_label(order.payment_method),
            _department_name(department_names, order.department),
        ])
    _finish(debt_ws, (10, 19, 30, 18, 22, 12, 18, 18, 18, 10, 20, 18), (7, 8, 9), (2,))

    output = BytesIO()
    _neutralize_formula_cells(wb)
    wb.save(output)
    return output.getvalue()
