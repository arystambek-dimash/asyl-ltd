from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.common.money import ZERO
from apps.common.pdf import money_text, para_text, register_fonts
from apps.orders.debt import order_remaining
from apps.orders.labels import (
    order_payment_method_label,
    payment_status_label,
    transport_label,
)
from apps.orders.statuses import public_status_label

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

INK = colors.HexColor("#101828")
MUTED = colors.HexColor("#667085")
RULE = colors.HexColor("#E4E7EC")
BAND = colors.HexColor("#F9FAFB")
PANEL = colors.HexColor("#F2F4F7")
DEBIT = "#B42318"   # начисление (долг растёт)
CREDIT = "#067647"  # оплата (долг гасится)
ROLE_COLOURS = {"debit": DEBIT, "credit": CREDIT}
RIGHT_KINDS = ("number", "money", "signed")


def _signed(value) -> str:
    amount = Decimal(value or 0)
    return f"{'+' if amount > 0 else ''}{money_text(amount)}"


def _stamp(value) -> str:
    local = local_time(value)
    return local.strftime("%d.%m.%Y %H:%M") if local else "—"


class _Styles:
    def __init__(self):
        base = getSampleStyleSheet()
        self.body = ParagraphStyle(
            "StBody",
            parent=base["BodyText"],
            fontName="InvoiceSans",
            fontSize=8,
            leading=10,
            textColor=INK,
        )
        self.bold = ParagraphStyle(
            "StBold", parent=self.body, fontName="InvoiceSans-Bold"
        )
        self.small = ParagraphStyle(
            "StSmall",
            parent=self.body,
            fontSize=7,
            leading=8.5,
            textColor=MUTED,
        )
        self.right = ParagraphStyle(
            "StRight", parent=self.body, alignment=TA_RIGHT
        )
        self.right_bold = ParagraphStyle(
            "StRightBold", parent=self.bold, alignment=TA_RIGHT
        )
        self.h1 = ParagraphStyle(
            "StH1", parent=self.bold, fontSize=16, leading=19
        )
        self.h2 = ParagraphStyle(
            "StH2", parent=self.bold, fontSize=10.5, leading=13
        )


def _table(rows, widths, *, aligns=None, header=True, total_row=False):
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), "InvoiceSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        commands += [
            ("FONTNAME", (0, 0), (-1, 0), "InvoiceSans-Bold"),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("BACKGROUND", (0, 0), (-1, 0), PANEL),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ]
        for index in range(1, len(rows)):
            if index % 2 == 0:
                commands.append(("BACKGROUND", (0, index), (-1, index), BAND))
    if total_row and len(rows) > 1:
        commands += [
            ("FONTNAME", (0, -1), (-1, -1), "InvoiceSans-Bold"),
            ("BACKGROUND", (0, -1), (-1, -1), PANEL),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ]
    for column, alignment in (aligns or {}).items():
        commands.append(("ALIGN", (column, 0), (column, -1), alignment))
    table.setStyle(TableStyle(commands))
    return table


def _cells(values, styles, *, right=()):
    return [
        Paragraph(para_text(value), styles.right if index in right else styles.body)
        for index, value in enumerate(values)
    ]


def _cell(column: Column, row, styles):
    value = column.value(row)
    if column.kind == "signed":
        colour = DEBIT if value > 0 else CREDIT
        return Paragraph(
            f'<font color="{colour}">{_signed(value)}</font>', styles.right
        )
    if column.kind == "money":
        value = money_text(value)
    elif column.kind == "date":
        value = _stamp(value)
    style = styles.right if column.kind in RIGHT_KINDS else styles.body
    return Paragraph(para_text(value), style)


def _column_table(columns: list[Column], rows, styles, *, compact_header=False):
    """Таблица по спецификации колонок; суммы и количества — вправо."""
    right = tuple(
        index for index, column in enumerate(columns)
        if column.kind in RIGHT_KINDS
    )
    headers = [column.header for column in columns]
    header = (
        [Paragraph(f"<b>{para_text(name)}</b>", styles.small) for name in headers]
        if compact_header
        else _cells(headers, styles, right=right)
    )
    return _table(
        [header, *([_cell(column, row, styles) for column in columns] for row in rows)],
        [column.width * mm for column in columns],
        aligns={index: "RIGHT" for index in right},
    )


def _section(story, styles, data: StatementData, titles, columns, rows) -> None:
    """Раздел-таблица; ``titles`` — заголовок выписки клиента и общей выписки."""
    _start_section(story, styles, titles[data.client is None])
    story.append(_column_table(columns_for(columns, data), rows, styles))


def _reconciliation_block(styles, data: StatementData, currency):
    lines = reconciliation_lines(data, currency)
    rows = []
    for index, (label, value, role) in enumerate(lines):
        last = index == len(lines) - 1
        amount = (
            f'<font color="{ROLE_COLOURS[role]}">{_signed(value)}</font>'
            if role else money_text(value)
        )
        rows.append([
            Paragraph(para_text(label), styles.bold if last else styles.body),
            Paragraph(amount, styles.right_bold if last else styles.right),
        ])
    return KeepTogether(
        [
            Paragraph(
                f"Краткое содержание операций · {para_text(currency)}", styles.h2
            ),
            Spacer(1, 2 * mm),
            _table(
                rows,
                [110 * mm, 40 * mm],
                aligns={1: "RIGHT"},
                header=False,
                total_row=True,
            ),
            Spacer(1, 5 * mm),
        ]
    )


def _build(story, styles, title, subtitle):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
        author="ASYL LTD",
    )

    def _page(canvas, document):
        canvas.saveState()
        canvas.setFont("InvoiceSans", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(12 * mm, 7 * mm, title)
        canvas.drawRightString(
            document.pagesize[0] - 12 * mm,
            7 * mm,
            f"Стр. {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    head = [
        Paragraph(para_text(title), styles.h1),
        Paragraph(para_text(subtitle), styles.small),
        HRFlowable(
            width="100%",
            thickness=1.2,
            color=INK,
            spaceBefore=2 * mm,
            spaceAfter=4 * mm,
        ),
    ]
    doc.build(head + story, onFirstPage=_page, onLaterPages=_page)
    return buffer.getvalue()


def _start_section(story, styles, title) -> None:
    """Start a section without creating an empty first document page."""
    if story:
        story.append(PageBreak())
    story.extend(
        [
            Paragraph(para_text(title), styles.h2),
            Spacer(1, 3 * mm),
        ]
    )


def _ledger_opening_block(styles, data: StatementData):
    """Show the balances that seed the running ledger, even with no movements."""
    if data.client is None:
        values = [
            (
                client.name,
                currency,
                data.client_opening[(client.id, currency)],
            )
            for client in data.clients
            for currency in data.currencies
            if data.client_opening.get((client.id, currency), ZERO)
        ]
        if not values:
            values = [
                ("Все клиенты", currency, data.opening.get(currency, ZERO))
                for currency in data.currencies
            ]
        rows = [
            _cells(
                ["Клиент", "Вал.", "Входящий остаток"],
                styles,
                right=(2,),
            )
        ]
        rows.extend(
            _cells([name, currency, money_text(opening)], styles, right=(2,))
            for name, currency, opening in values
        )
        widths = [85 * mm, 20 * mm, 45 * mm]
        aligns = {2: "RIGHT"}
    else:
        rows = [
            _cells(["Вал.", "Входящий остаток"], styles, right=(1,))
        ]
        rows.extend(
            _cells(
                [currency, money_text(data.opening.get(currency, ZERO))],
                styles,
                right=(1,),
            )
            for currency in data.currencies
        )
        widths = [45 * mm, 55 * mm]
        aligns = {1: "RIGHT"}
    return KeepTogether(
        [
            Paragraph("Входящий остаток", styles.h2),
            Spacer(1, 2 * mm),
            _table(rows, widths, aligns=aligns),
            Spacer(1, 5 * mm),
        ]
    )


def _summary(story, styles, data: StatementData) -> None:
    info = [["Отделы", data.department_scope, "Период", data.period]]
    client = data.client
    if client is not None:
        info = [
            ["Клиент", client.name, "Телефон", client.phone],
            ["ИИН / БИН", client.iin or "—", "Страна", client.country or "—"],
            *info,
        ]
    story.extend(
        [
            _table(
                [
                    [
                        Paragraph(f"<b>{para_text(row[0])}</b>", styles.small),
                        Paragraph(para_text(row[1]), styles.body),
                        Paragraph(f"<b>{para_text(row[2])}</b>", styles.small),
                        Paragraph(para_text(row[3]), styles.body),
                    ]
                    for row in info
                ],
                [28 * mm, 95 * mm, 28 * mm, 70 * mm],
                header=False,
            ),
            Spacer(1, 6 * mm),
        ]
    )
    story.extend(
        _reconciliation_block(styles, data, currency)
        for currency in data.currencies
    )


def _clients(story, styles, data: StatementData) -> None:
    # Снимок заводит строку на каждого клиента в каждой валюте выписки:
    # пропускаем пары без движения, а не только отсутствующие.
    rows = [
        (client, currency, data.client_totals[(client.id, currency)])
        for client in data.clients
        for currency in data.currencies
        if any((data.client_totals.get((client.id, currency)) or {}).values())
    ]
    _section(story, styles, data, (None, "Клиенты"), [
        Column("Клиент", 64, lambda row: row[0].name),
        Column("Телефон", 32, lambda row: row[0].phone),
        Column("Вал.", 12, lambda row: row[1]),
        Column("Заказов", 22, lambda row: row[2]["orders"], "number"),
        Column("Продажи", 34, lambda row: row[2]["sales"], "money"),
        Column("Оплачено", 34, lambda row: row[2]["payments"], "money"),
        Column("Долг", 34, lambda row: row[2]["debt"], "money"),
    ], rows)


def _ledger(story, styles, data: StatementData) -> None:
    """Canonical operations with the running balance prepared by ``data``."""
    _start_section(story, styles, "Операции")
    story.append(_ledger_opening_block(styles, data))
    columns = columns_for([
        Column("Дата", (26, 24), lambda row: row[0].occurred_at, "date"),
        Column("Клиент", (None, 40), lambda row: row[0].order.client.name),
        Column("Операция", (30, 26), lambda row: row[1].label),
        Column("Заказ", (14, 13), lambda row: row[0].order.id),
        Column("Описание", (78, 58), lambda row: row[1].description),
        Column("Способ / статус", (30, 26), lambda row: row[1].method),
        Column("Вал.", (12, 11), lambda row: row[0].order.currency),
        Column("Сумма", (25, 24), lambda row: row[0].amount, "signed"),
        Column("Остаток", (25, 24), lambda row: row[0].balance_after, "money"),
    ], data)
    rows = [
        (operation, operation_display(operation))
        for operation in data.operations
    ]
    story.append(_column_table(columns, rows, styles, compact_header=True))


def _orders(story, styles, data: StatementData) -> None:
    _section(story, styles, data, ("Заказы", "Все заказы"), [
        Column("№", 14, lambda order: order.id),
        Column("Создан", 26, lambda order: order.created_at, "date"),
        Column("Клиент", (None, 50), lambda order: order.client.name),
        Column("Статус", 26, lambda order: public_status_label(order.status)),
        Column("Отгружен", (26, None), shipped_at, "date"),
        Column("Отдел", 26, lambda order: department_name(data, order.department)),
        Column(
            "Транспорт", (22, None),
            lambda order: transport_label(order.transport_type),
        ),
        Column("Вал.", 12, lambda order: order.currency),
        Column("Сумма", 28, lambda order: order.total_amount, "money"),
        Column("Оплачено", 28, lambda order: order.paid_total, "money"),
        Column("Долг", (28, 26), order_remaining, "money"),
    ], data.orders)


def _items(story, styles, data: StatementData) -> None:
    _section(story, styles, data, ("Позиции заказов", "Позиции всех заказов"), [
        Column("Заказ", 16, lambda row: row[0].id),
        Column("Дата", (28, None), lambda row: row[0].created_at, "date"),
        Column("Клиент", (None, 50), lambda row: row[0].client.name),
        Column("Товар", (90, 74), lambda row: row[1].product_label),
        Column("Мешков", (22, 20), lambda row: row[1].quantity, "number"),
        Column("Цена", (30, 28), lambda row: row[1].unit_price, "money"),
        Column(
            "Сумма", (34, 32),
            lambda row: row[1].quantity * (row[1].unit_price or 0), "money",
        ),
        Column("Вал.", 14, lambda row: row[0].currency),
    ], [(order, item) for order in data.orders for item in order.items.all()])


def _payments(story, styles, data: StatementData) -> None:
    _section(story, styles, data, ("Платежи", "Все платежи"), [
        Column("№", (16, 14), lambda payment: payment.id),
        Column("Дата", (28, 26), lambda payment: payment.recognized_at, "date"),
        Column("Клиент", (None, 50), lambda payment: payment.order.client.name),
        Column("Заказ", 16, lambda payment: payment.order_id),
        Column("Способ", (34, 32), lambda payment: method_label(payment.method)),
        Column(
            "Статус", (30, 28),
            lambda payment: payment_status_label(payment.status),
        ),
        Column("Сумма", 32, lambda payment: payment.amount, "money"),
        Column("Вал.", 12, lambda payment: payment.order.currency),
        Column(
            "Сотрудник", (30, None), lambda payment: username(payment.author),
        ),
    ], data.payments)


def _debts(story, styles, data: StatementData) -> None:
    _section(story, styles, data, ("Текущие долги", "Текущие долги"), [
        Column("Заказ", 16, lambda order: order.id),
        Column("Клиент", (None, 54), lambda order: order.client.name),
        Column("Отгружен", 28, lambda order: order.sale_at, "date"),
        Column(
            "Магазин", (40, None),
            lambda order: order.store.name if order.store else "—",
        ),
        Column("Сумма", (30, 32), lambda order: order.total_amount, "money"),
        Column("Оплачено", (30, 32), lambda order: order.paid_total, "money"),
        Column("Остаток", (30, 32), order_remaining, "money"),
        Column("Вал.", (12, 14), lambda order: order.currency),
        Column(
            "Способ", (30, None),
            lambda order: order_payment_method_label(order.payment_method),
        ),
    ], data.debt_orders)


SECTIONS = {
    "summary": _summary,
    "clients": _clients,
    "ledger": _ledger,
    "orders": _orders,
    "items": _items,
    "payments": _payments,
    "debts": _debts,
}


def render_statement_pdf(data: StatementData) -> bytes:
    """Render a prepared statement (one client or all clients) without the ORM."""
    register_fonts()
    styles = _Styles()
    story: list = []
    for section in data.sections:
        SECTIONS[section](story, styles, data)
    if not story:
        story = [Paragraph("Нет данных за выбранный период.", styles.body)]
    title = (
        "Выписка по клиенту" if data.client is not None
        else "Общая выписка по клиентам"
    )
    return _build(story, styles, title, data.subtitle)
