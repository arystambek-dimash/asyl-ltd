"""XLSX rendering for client statements."""

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.orders.debt import order_remaining
from apps.orders.labels import (
    order_payment_method_label,
    payment_status_label,
    transport_label,
)
from apps.orders.statuses import public_status_label
from apps.orders.transport import transport_cell_text

from .data import StatementData, department_name, local_time
from .presentation import (
    Column,
    columns_for,
    method_label,
    operation_display,
    reconciliation_lines,
    shipped_at,
    username,
)

FORMULA_PREFIXES = ("=", "+", "-", "@")

# ── Оформление выписки ────────────────────────────────────────────────────
# Документ, а не дашборд: тёмный текст на белом, одна акцентная линия и
# серая заливка итогов. Цветом отмечены только знак суммы и итоговые строки,
# поэтому лист остаётся читаемым и в печати, и в ч/б.
INK = "101828"          # основной текст
MUTED = "667085"        # подписи, вторичные значения
HAIRLINE = "E4E7EC"     # линии таблицы
BAND = "F9FAFB"         # чередование строк
PANEL = "F2F4F7"        # заливка блока сверки
ACCENT = "17233B"       # шапка листа
DEBIT = "B42318"        # начисление (долг растёт)
CREDIT = "067647"       # оплата (долг гасится)

RULE = Side(style="thin", color=HAIRLINE)
STRONG_RULE = Side(style="medium", color=ACCENT)

# Знак суммы в ленте: отгрузка наращивает долг клиента (+), оплата гасит (−).
# Выбор в пользу «баланс = сколько клиент должен» — положительное число,
# как это читают менеджеры. Kaspi показывает зеркальную картину (взгляд
# со стороны владельца счёта), но здесь владелец отчёта — продавец.
MONEY_FORMAT = '#,##0.00'
SIGNED_FORMAT = '+#,##0.00;-#,##0.00;0.00'


def _money(value):
    """Денежное значение для ячейки Excel.

    openpyxl пишет Decimal нативно, а float на суммах в миллионы тенге теряет
    копейки (99999999.99 хранится как 99999999.98999999…): выписка клиента
    начинала расходиться с API. Формат ячейки задаёт `_finish`.
    """
    return Decimal(value or 0)


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
    """Шапка листа: название, период и тонкая акцентная линия под ними.

    Заголовок печатается на каждой странице (как «Приложение к справке»
    в банковской выписке), поэтому многостраничная печать остаётся читаемой.
    """
    ws.sheet_view.showGridLines = False
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    cell = ws.cell(1, 1, title)
    cell.font = Font(name="Calibri", size=20, bold=True, color=INK)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=columns)
    note = ws.cell(2, 1, subtitle)
    note.font = Font(size=9, color=MUTED)
    note.alignment = Alignment(vertical="center")
    ws.row_dimensions[2].height = 18
    for col in range(1, columns + 1):
        ws.cell(2, col).border = Border(bottom=STRONG_RULE)

    ws.print_title_rows = "1:3"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.oddFooter.right.text = "Стр. &P из &N"
    ws.oddFooter.right.size = 8
    ws.oddFooter.right.color = MUTED
    ws.oddFooter.left.text = title
    ws.oddFooter.left.size = 8
    ws.oddFooter.left.color = MUTED


def _headers(ws, row, values):
    for col, value in enumerate(values, 1):
        cell = ws.cell(row, col, value)
        cell.font = Font(bold=True, size=9, color=MUTED)
        cell.alignment = Alignment(vertical="bottom", wrap_text=True)
        cell.border = Border(bottom=STRONG_RULE)
    ws.row_dimensions[row].height = 26
    ws.freeze_panes = f"A{row + 1}"
    ws.auto_filter.ref = f"A{row}:{get_column_letter(len(values))}{row}"


def _info(ws, lines):
    """Карточка «подпись — значение» под шапкой; возвращает строку после отбивки."""
    for row, (label, value) in enumerate(lines, 4):
        ws.cell(row, 1, label).font = Font(size=10, color=MUTED)
        ws.cell(row, 2, value).font = Font(size=10, bold=True, color=INK)
        ws.row_dimensions[row].height = 18
    return 4 + len(lines) + 1


def _summary_block(ws, row, title, lines, *, emphasize_last=False):
    """Блок «Краткое содержание операций» в стиле банковской выписки.

    ``lines`` — последовательность ``(подпись, значение, роль)``, где роль
    управляет только цветом: ``"debit"``/``"credit"``/``None``. При
    ``emphasize_last`` последняя строка отбивается заливкой и рамкой — это
    исходящий остаток, к которому обязана сходиться арифметика блока.
    """
    header = ws.cell(row, 1, title)
    header.font = Font(bold=True, size=10, color=INK)
    header.alignment = Alignment(vertical="center")
    ws.cell(row, 1).border = Border(bottom=RULE)
    ws.cell(row, 2).border = Border(bottom=RULE)
    ws.row_dimensions[row].height = 22

    cursor = row + 1
    colors = {"debit": DEBIT, "credit": CREDIT}
    for label, value, role in lines:
        name = ws.cell(cursor, 1, label)
        name.font = Font(size=10, color=INK)
        name.alignment = Alignment(vertical="center", indent=1)
        amount = ws.cell(cursor, 2, value)
        amount.number_format = SIGNED_FORMAT if role else MONEY_FORMAT
        amount.alignment = Alignment(vertical="center", horizontal="right")
        amount.font = Font(size=10, color=colors.get(role, INK))
        for col in (1, 2):
            ws.cell(cursor, col).border = Border(bottom=RULE)
        ws.row_dimensions[cursor].height = 19
        cursor += 1

    if emphasize_last:
        for col in (1, 2):
            cell = ws.cell(cursor - 1, col)
            cell.fill = PatternFill("solid", fgColor=PANEL)
            cell.border = Border(top=RULE, bottom=STRONG_RULE)
            cell.font = Font(
                size=10, bold=True,
                color=cell.font.color.rgb if cell.font.color else INK,
            )
        ws.cell(cursor - 1, 1).font = Font(size=10, bold=True, color=INK)
    return cursor


def _finish(
    ws, widths, money_columns=(), date_columns=(), *,
    first_row=4, signed_columns=(),
):
    """Отделка таблицы: ширины, полосы, форматы чисел и дат.

    ``first_row`` сдвигается, когда над таблицей стоит блок сверки.
    ``signed_columns`` печатаются со знаком и подкрашиваются: начисление
    красным, погашение зелёным — как в ленте банковской выписки.
    """
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for row in ws.iter_rows(min_row=first_row):
        for cell in row:
            cell.border = Border(bottom=RULE)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.font = Font(size=10, color=INK)
            if (cell.row - first_row) % 2 == 1:
                cell.fill = PatternFill("solid", fgColor=BAND)
    for col in money_columns:
        for cell in ws[get_column_letter(col)][first_row - 1:]:
            cell.number_format = MONEY_FORMAT
            cell.alignment = Alignment(vertical="center", horizontal="right")
    for col in signed_columns:
        for cell in ws[get_column_letter(col)][first_row - 1:]:
            cell.number_format = SIGNED_FORMAT
            cell.alignment = Alignment(vertical="center", horizontal="right")
            value = cell.value
            if isinstance(value, Decimal) and value:
                cell.font = Font(
                    size=10, color=DEBIT if value > 0 else CREDIT)
    for col in date_columns:
        for cell in ws[get_column_letter(col)][first_row - 1:]:
            cell.number_format = "dd.mm.yyyy hh:mm"


def _plain_rows(ws, first_row, money_columns):
    """Строки таблицы «Сводки» без полос: линия, шрифт и денежный формат."""
    for row in ws.iter_rows(min_row=first_row, max_row=ws.max_row):
        for cell in row:
            cell.border = Border(bottom=RULE)
            cell.font = Font(size=10, color=INK)
        for cell in row[money_columns]:
            cell.number_format = MONEY_FORMAT
            cell.alignment = Alignment(horizontal="right")


def _cell_value(column: Column, row):
    value = column.value(row)
    if column.kind == "date":
        return local_time(value)
    if column.kind in ("money", "signed"):
        return _money(value)
    return value


def _table(ws, header_row, columns: list[Column], rows) -> None:
    """Таблица по спецификации колонок: заголовки, строки и оформление."""
    _headers(ws, header_row, [column.header for column in columns])
    for row in rows:
        ws.append([_cell_value(column, row) for column in columns])

    def positions(kind):
        return tuple(
            index for index, column in enumerate(columns, 1)
            if column.kind == kind
        )

    _finish(
        ws, [column.width for column in columns],
        money_columns=positions("money"), date_columns=positions("date"),
        first_row=header_row + 1, signed_columns=positions("signed"),
    )


def _table_sheet(wb, data, name, titles, columns, rows) -> None:
    """Лист-таблица; ``titles`` — заголовок выписки клиента и общей выписки."""
    columns = columns_for(columns, data)
    ws = wb.create_sheet(name)
    _title(ws, titles[data.client is None], data.subtitle, len(columns))
    _table(ws, 3, columns, rows)


def _client_columns(client_of, width=30):
    """«Клиент» и «Телефон» — только в общей выписке."""
    return [
        Column("Клиент", (None, width), lambda row: client_of(row).name),
        Column("Телефон", (None, 18), lambda row: client_of(row).phone),
    ]


def _reconciliation_blocks(ws, row, data: StatementData) -> int:
    """Блок сверки по каждой валюте: вх. остаток + начислено − оплачено."""
    for currency in data.currencies:
        row = _summary_block(
            ws, row,
            f"Краткое содержание операций · {currency}",
            [
                (label, _money(value), role)
                for label, value, role in reconciliation_lines(data, currency)
            ],
            emphasize_last=True,
        ) + 1
    return row


def _currency_table(ws, row, data: StatementData, last_header, last_value):
    _headers(ws, row, [
        "Валюта", "Заказов", "Продажи", "Оплачено",
        "Остаток на начало", "Остаток на конец", last_header,
    ])
    for currency in data.currencies:
        totals = data.totals[currency]
        ws.append([
            currency, totals["orders"],
            _money(totals["sales"]), _money(totals["payments"]),
            _money(data.opening[currency]), _money(data.closing[currency]),
            last_value(currency),
        ])


def _client_summary_sheet(wb, data: StatementData) -> None:
    client = data.client
    ws = wb.create_sheet("Сводка")
    _title(ws, "Выписка по клиенту", data.subtitle, 6)
    cursor = _info(ws, [
        ("Клиент", client.name),
        ("Телефон", client.phone),
        ("ИИН / БИН", client.iin or "—"),
        ("Страна", client.country or "—"),
        ("Банк", client.bank or "—"),
        ("Отделы", data.department_scope),
        ("Период", data.period),
    ])
    table_row = _reconciliation_blocks(ws, cursor, data)
    _currency_table(
        ws, table_row, data, "Текущий долг",
        lambda currency: _money(data.totals[currency]["debt"]),
    )
    _finish(
        ws, (26, 16, 18, 18, 20, 20, 18),
        (3, 4, 5, 6, 7), first_row=table_row + 1,
    )
    ws.freeze_panes = "A4"


def _all_summary_sheet(wb, data: StatementData) -> None:
    ws = wb.create_sheet("Сводка")
    _title(ws, "Общая выписка по клиентам", data.subtitle, 7)
    cursor = _info(ws, [
        ("Клиентов", len(data.clients)),
        ("Заказов", len(data.orders)),
        ("Платежей", len(data.payments)),
        ("Отделы", data.department_scope),
        ("Период", data.period),
    ])
    currency_row = _reconciliation_blocks(ws, cursor, data)
    _currency_table(
        ws, currency_row, data, "Клиентов с долгом",
        lambda currency: sum(
            data.client_totals[(client.id, currency)]["debt"] > 0
            for client in data.clients
        ),
    )
    _plain_rows(ws, currency_row + 1, slice(2, 6))

    # Leave one visual separator row between currencies and departments.
    department_header_row = ws.max_row + 2
    _headers(ws, department_header_row, [
        "Отдел", "Валюта", "Заказов", "Продажи", "Оплачено",
        "Текущий долг", "Движение за период",
    ])
    for code in data.department_codes:
        for currency in data.currencies:
            row_totals = data.department_totals[(code, currency)]
            ws.append([
                department_name(data, code),
                currency,
                row_totals["orders"],
                _money(row_totals["sales"]),
                _money(row_totals["payments"]),
                _money(row_totals["debt"]),
                _money(row_totals["sales"] - row_totals["payments"]),
            ])
    _plain_rows(ws, department_header_row + 1, slice(3, 7))
    for col, width in enumerate((26, 14, 16, 20, 20, 20, 22), 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A4"


def _clients_sheet(wb, data: StatementData) -> None:
    def total(currency, field):
        return lambda client: data.client_totals[(client.id, currency)][field]

    _table_sheet(wb, data, "Клиенты", (None, "Клиенты"), [
        Column("ID", 8, lambda client: client.id),
        Column("Клиент", 30, lambda client: client.name),
        Column("Компания", 28, lambda client: client.company_name or "—"),
        Column("Телефон", 19, lambda client: client.phone),
        Column("ИИН / БИН", 16, lambda client: client.iin or "—"),
        Column("Страна", 18, lambda client: client.country or "—"),
        Column("Валюта прайса", 15, lambda client: client.currency),
        *(
            Column(f"{header} {currency}", width, total(currency, field), kind)
            for currency in data.currencies
            for header, width, field, kind in (
                ("Заказов", 14, "orders", "number"),
                ("Продажи", 18, "sales", "money"),
                ("Оплачено", 18, "payments", "money"),
                ("Долг", 18, "debt", "money"),
            )
        ),
    ], data.clients)


def _ledger_sheet(wb, data: StatementData) -> None:
    # Лента в стиле банковской выписки: одна знаковая сумма и текущий
    # остаток после каждой операции.
    columns = columns_for([
        Column("Дата", 19, lambda row: row[0].occurred_at, "date"),
        *_client_columns(lambda row: row[0].order.client, width=28),
        Column("Операция", 21, lambda row: row[1].label),
        Column("Заказ", 10, lambda row: row[0].order.id),
        Column("Описание", (44, 42), lambda row: row[1].description),
        Column("Способ / статус", 22, lambda row: row[1].method),
        Column("Валюта", 10, lambda row: row[0].order.currency),
        Column("Сумма", 18, lambda row: row[0].amount, "signed"),
        Column("Остаток", (18, None), lambda row: row[0].balance_after, "money"),
        Column(
            "Остаток клиента", (None, 20),
            lambda row: row[0].balance_after, "money",
        ),
        Column("Автор", 20, lambda row: username(row[1].author)),
        Column(
            "Отдел", 22,
            lambda row: department_name(data, row[0].order.department),
        ),
    ], data)
    ws = wb.create_sheet("Операции")
    _title(ws, "Операции", data.subtitle, len(columns))
    header_row = _summary_block(ws, 4, "Входящий остаток", [
        (
            f"Остаток на начало периода · {currency}",
            _money(data.opening[currency]),
            None,
        )
        for currency in data.currencies
    ]) + 1
    _table(ws, header_row, columns, [
        (operation, operation_display(operation))
        for operation in data.operations
    ])


def _orders_sheet(wb, data: StatementData) -> None:
    _table_sheet(wb, data, "Заказы", ("Заказы", "Все заказы"), [
        Column("№", 9, lambda order: order.id),
        Column("Создан", 19, lambda order: order.created_at, "date"),
        *_client_columns(lambda order: order.client),
        Column("Статус", 20, lambda order: public_status_label(order.status)),
        Column("Отгружен", 19, shipped_at, "date"),
        Column("Отдел", 18, lambda order: department_name(data, order.department)),
        Column("Магазин", 22, lambda order: order.store.name if order.store else "—"),
        Column("Транспорт", 12, lambda order: transport_label(order.transport_type)),
        Column("Номер", 16, lambda order: transport_cell_text(order) or "—"),
        Column("Валюта", 10, lambda order: order.currency),
        Column("Сумма", 16, lambda order: order.total_amount, "money"),
        Column("Оплачено", 16, lambda order: order.paid_total, "money"),
        Column("Долг", 16, order_remaining, "money"),
        Column("Мешков", 12, lambda order: order.ordered_bags, "number"),
        Column("Повтор заказа", (15, 16), lambda order: order.repeated_from_id),
        Column("Примечание", 35, lambda order: order.notes),
    ], data.orders)


def _items_sheet(wb, data: StatementData) -> None:
    _table_sheet(wb, data, "Позиции", ("Позиции заказов", "Позиции всех заказов"), [
        Column("Заказ", 10, lambda row: row[0].id),
        Column("Дата", 19, lambda row: row[0].created_at, "date"),
        *_client_columns(lambda row: row[0].client),
        Column("Товар", 40, lambda row: row[1].product_label),
        Column("Класс CV", 16, lambda row: row[1].product_cv_class or "—"),
        Column("Мешков", 12, lambda row: row[1].quantity, "number"),
        Column("Цена / мешок", 18, lambda row: row[1].unit_price, "money"),
        Column(
            "Сумма", 18,
            lambda row: row[1].quantity * (row[1].unit_price or 0), "money",
        ),
        Column("Валюта", 10, lambda row: row[0].currency),
        Column("Отдел", 22, lambda row: department_name(data, row[0].department)),
    ], [(order, item) for order in data.orders for item in order.items.all()])


def _payments_sheet(wb, data: StatementData) -> None:
    _table_sheet(wb, data, "Платежи", ("Платежи", "Все платежи"), [
        Column("№", 9, lambda payment: payment.id),
        Column("Дата", 19, lambda payment: payment.recognized_at, "date"),
        *_client_columns(lambda payment: payment.order.client),
        Column("Заказ", 10, lambda payment: payment.order_id),
        Column("Способ", 20, lambda payment: method_label(payment.method)),
        Column("Статус", 18, lambda payment: payment_status_label(payment.status)),
        Column("Сумма", 18, lambda payment: payment.amount, "money"),
        Column("Валюта", 10, lambda payment: payment.order.currency),
        Column("Сотрудник", 20, lambda payment: username(payment.author)),
        Column("Примечание", 38, lambda payment: payment.note),
        Column(
            "Отдел", 22,
            lambda payment: department_name(data, payment.order.department),
        ),
    ], data.payments)


def _debts_sheet(wb, data: StatementData) -> None:
    _table_sheet(wb, data, "Долги", ("Текущие долги", "Текущие долги"), [
        Column("Заказ", 10, lambda order: order.id),
        Column("Отгружен", 19, lambda order: order.sale_at, "date"),
        *_client_columns(lambda order: order.client),
        Column("Магазин", 22, lambda order: order.store.name if order.store else "—"),
        Column("Мешков", 12, lambda order: order.ordered_bags, "number"),
        Column("Сумма", 18, lambda order: order.total_amount, "money"),
        Column("Оплачено", 18, lambda order: order.paid_total, "money"),
        Column("Остаток", 18, order_remaining, "money"),
        Column("Валюта", 10, lambda order: order.currency),
        Column(
            "Способ", 20,
            lambda order: order_payment_method_label(order.payment_method),
        ),
        Column(
            "Отдел", (22, 18),
            lambda order: department_name(data, order.department),
        ),
    ], data.debt_orders)


def render_statement_xlsx(data: StatementData) -> bytes:
    """Отрисовать готовый снимок выписки (клиента или общей) без запросов к БД."""
    sheets = {
        "summary": (
            _client_summary_sheet if data.client is not None
            else _all_summary_sheet
        ),
        "clients": _clients_sheet,
        "ledger": _ledger_sheet,
        "orders": _orders_sheet,
        "items": _items_sheet,
        "payments": _payments_sheet,
        "debts": _debts_sheet,
    }
    workbook = Workbook()
    workbook.remove(workbook.active)
    for section in data.sections:
        sheets[section](workbook, data)
    output = BytesIO()
    _neutralize_formula_cells(workbook)
    workbook.save(output)
    return output.getvalue()
