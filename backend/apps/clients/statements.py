"""Excel-выписка клиента: заказы, продажи, оплаты и текущие долги."""
from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.orders.models import Order, Payment
from apps.orders.statuses import public_status_label


METHOD_LABELS = {
    "invoice": "Счёт на оплату", "kaspi": "Kaspi", "cash": "Наличными",
    "debt": "В долг", "card": "Карта (архив)",
}
PAYMENT_STATUS_LABELS = {
    "requested": "Запрошена", "received": "Получена",
    "confirmed": "Подтверждена", "rejected": "Отклонена",
}

NAVY = "17233B"
BLUE = "3367D6"
LIGHT_BLUE = "EAF1FF"
LIGHT_GRAY = "F4F6F9"
GREEN = "1F9D6A"
RED = "D94C3D"
WHITE = "FFFFFF"
THIN = Side(style="thin", color="D9E0EA")


def _local(value):
    return timezone.localtime(value).replace(tzinfo=None) if value else None


def _money(value):
    return float(value or Decimal("0"))


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


def build_client_statement(client, date_from=None, date_to=None) -> bytes:
    orders = (
        Order.objects.filter(client=client)
        .select_related("store", "shipment", "repeated_from")
        .prefetch_related(
            "items__product", "payments__recorded_by", "payments__received_by",
            "payments__confirmed_by",
        )
        .order_by("created_at", "id")
    )
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)
    orders = list(orders)
    payments = Payment.objects.filter(
        order__client=client, order__deleted_at__isnull=True,
    ).select_related("order", "recorded_by", "received_by", "confirmed_by")
    if date_from:
        payments = payments.filter(paid_at__date__gte=date_from)
    if date_to:
        payments = payments.filter(paid_at__date__lte=date_to)
    payments = list(payments.order_by("paid_at", "id"))

    period = "за всё время"
    if date_from or date_to:
        period = f"{date_from.strftime('%d.%m.%Y') if date_from else 'начала'} — " \
                 f"{date_to.strftime('%d.%m.%Y') if date_to else 'сегодня'}"
    subtitle = f"{client.name} · {period} · сформировано {timezone.localtime():%d.%m.%Y %H:%M}"

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

    totals = defaultdict(lambda: {
        "orders": 0, "sales": Decimal("0"), "payments": Decimal("0"),
        "debt": Decimal("0"),
    })
    for order in orders:
        row = totals[order.currency]
        row["orders"] += 1
        if order.status == "shipped":
            row["sales"] += order.total_amount
            if order.is_debt:
                row["debt"] += max(Decimal("0"), order.remaining_amount)
    for payment in payments:
        if payment.status == "confirmed":
            totals[payment.order.currency]["payments"] += payment.amount

    ws.append([])
    ws.append(["Валюта", "Заказов", "Продажи", "Оплачено", "Текущий долг", "Баланс выписки"])
    for cell in ws[7]:
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=BLUE)
    for currency in ("KZT", "USD"):
        row = totals[currency]
        ws.append([
            currency, row["orders"], _money(row["sales"]),
            _money(row["payments"]), _money(row["debt"]),
            _money(row["sales"] - row["payments"]),
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
    _title(ledger, "Операции", subtitle, 10)
    _headers(ledger, 3, [
        "Дата", "Операция", "Заказ", "Описание", "Способ / статус",
        "Валюта", "Начислено", "Оплачено", "Баланс", "Автор",
    ])
    operations = []
    for order in orders:
        if order.status == "shipped":
            stamp = getattr(getattr(order, "shipment", None), "shipped_at", None) or order.created_at
            operations.append((stamp, 0, order.currency, order.total_amount, Decimal("0"), order))
    for payment in payments:
        if payment.status == "confirmed":
            stamp = payment.confirmed_at or payment.paid_at
            operations.append((stamp, 1, payment.order.currency, Decimal("0"), payment.amount, payment))
    operations.sort(key=lambda item: (item[0], item[1], getattr(item[5], "id", 0)))
    balances = defaultdict(Decimal)
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
            ]
        else:
            payment = obj
            author = payment.confirmed_by or payment.received_by or payment.recorded_by
            values = [
                _local(stamp), "Оплата", payment.order_id, payment.note or "Поступление оплаты",
                METHOD_LABELS.get(payment.method, payment.method), currency, 0,
                _money(credit), _money(balances[currency]), author.username if author else "—",
            ]
        ledger.append(values)
    _finish(ledger, (19, 21, 10, 44, 22, 10, 16, 16, 16, 20), (7, 8, 9), (1,))

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
            _local(shipment.shipped_at) if shipment else None, order.department,
            order.store.name if order.store else "—",
            "Поезд" if order.transport_type == "train" else "Трак",
            order.truck_number or "—", order.currency, _money(order.total_amount),
            _money(order.paid_total), _money(max(Decimal("0"), order.remaining_amount)),
            order.repeated_from_id, order.notes,
        ])
    _finish(orders_ws, (9, 19, 20, 19, 18, 22, 12, 16, 10, 16, 16, 16, 15, 35), (10, 11, 12), (2, 4))

    items_ws = wb.create_sheet("Позиции")
    _title(items_ws, "Позиции заказов", subtitle, 8)
    _headers(items_ws, 3, [
        "Заказ", "Дата", "Товар", "Класс CV", "Мешков", "Цена / мешок", "Сумма", "Валюта",
    ])
    for order in orders:
        for item in order.items.all():
            items_ws.append([
                order.id, _local(order.created_at), item.product_label,
                item.product_cv_class or "—", item.quantity,
                _money(item.unit_price), _money(item.quantity * (item.unit_price or 0)),
                order.currency,
            ])
    _finish(items_ws, (10, 19, 40, 16, 12, 18, 18, 10), (6, 7), (2,))

    pay_ws = wb.create_sheet("Платежи")
    _title(pay_ws, "Платежи", subtitle, 9)
    _headers(pay_ws, 3, [
        "№", "Дата", "Заказ", "Способ", "Статус", "Сумма", "Валюта", "Сотрудник", "Примечание",
    ])
    for payment in payments:
        author = payment.confirmed_by or payment.received_by or payment.recorded_by
        pay_ws.append([
            payment.id, _local(payment.confirmed_at or payment.paid_at), payment.order_id,
            METHOD_LABELS.get(payment.method, payment.method),
            PAYMENT_STATUS_LABELS.get(payment.status, payment.status), _money(payment.amount),
            payment.order.currency, author.username if author else "—", payment.note,
        ])
    _finish(pay_ws, (9, 19, 10, 20, 18, 18, 10, 20, 38), (6,), (2,))

    debt_ws = wb.create_sheet("Долги")
    _title(debt_ws, "Текущие долги", subtitle, 9)
    _headers(debt_ws, 3, [
        "Заказ", "Отгружен", "Магазин", "Мешков", "Сумма", "Оплачено", "Остаток", "Валюта", "Способ",
    ])
    for order in orders:
        if not order.is_debt:
            continue
        shipment = getattr(order, "shipment", None)
        debt_ws.append([
            order.id, _local(shipment.shipped_at) if shipment else _local(order.created_at),
            order.store.name if order.store else "—",
            sum(item.quantity for item in order.items.all()), _money(order.total_amount),
            _money(order.paid_total), _money(order.remaining_amount), order.currency,
            METHOD_LABELS.get(order.payment_method, order.payment_method),
        ])
    _finish(debt_ws, (10, 19, 22, 12, 18, 18, 18, 10, 20), (5, 6, 7), (2,))

    output = BytesIO()
    wb.save(output)
    return output.getvalue()
