from decimal import Decimal
from html import escape
from io import BytesIO

from apps.orders.invoices import _register_fonts
from apps.orders.labels import (
    order_payment_method_label,
    payment_method_label,
    payment_status_label,
    transport_label,
)
from apps.orders.statuses import public_status_label
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

from .data import (
    StatementData,
    StatementOperation,
    build_statement_data,
    department_name,
    local_time,
)

INK = colors.HexColor("#101828")
MUTED = colors.HexColor("#667085")
RULE = colors.HexColor("#E4E7EC")
BAND = colors.HexColor("#F9FAFB")
PANEL = colors.HexColor("#F2F4F7")

ZERO = Decimal(0)


def _text(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _money(value) -> str:
    return f"{Decimal(value or 0):,.2f}".replace(",", " ")


def _signed(value) -> str:
    amount = Decimal(value or 0)
    return f"{'+' if amount > 0 else ''}{_money(amount)}"


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


def _table(rows, widths, styles, *, aligns=None, header=True, total_row=False):
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
        Paragraph(_text(value), styles.right if index in right else styles.body)
        for index, value in enumerate(values)
    ]


def _reconciliation_block(styles, currency, opening, charged, paid):
    closing = opening + charged - paid
    rows = [
        [
            Paragraph(
                f"Остаток на начало периода, {_text(currency)}", styles.body
            ),
            Paragraph(_money(opening), styles.right),
        ],
        [
            Paragraph("Начислено (отгрузки)", styles.body),
            Paragraph(
                f'<font color="#B42318">{_signed(charged)}</font>',
                styles.right,
            ),
        ],
        [
            Paragraph("Оплачено (поступления)", styles.body),
            Paragraph(
                f'<font color="#067647">{_signed(-paid)}</font>',
                styles.right,
            ),
        ],
        [
            Paragraph("Остаток на конец периода", styles.bold),
            Paragraph(_money(closing), styles.right_bold),
        ],
    ]
    return KeepTogether(
        [
            Paragraph(
                f"Краткое содержание операций · {_text(currency)}", styles.h2
            ),
            Spacer(1, 2 * mm),
            _table(
                rows,
                [110 * mm, 40 * mm],
                styles,
                aligns={1: "RIGHT"},
                header=False,
                total_row=True,
            ),
            Spacer(1, 5 * mm),
        ]
    )


def _operation_description(operation: StatementOperation) -> tuple[str, str]:
    if operation.kind == "sale":
        return "Продажа / отгрузка", public_status_label(operation.order.status)

    payment = operation.payment
    if payment is None:
        raise ValueError("Payment operation must contain a payment")
    return (
        "Оплата",
        payment_method_label(payment.method, archived_hint=True),
    )


def _ledger_rows(styles, data: StatementData, *, with_client: bool):
    """Render canonical operations and their presentation-time running balance."""
    header = [
        "Дата",
        "Операция",
        "Заказ",
        "Описание",
        "Способ / статус",
        "Вал.",
        "Сумма",
        "Остаток",
    ]
    if with_client:
        header.insert(1, "Клиент")
    rows = [[Paragraph(f"<b>{_text(name)}</b>", styles.small) for name in header]]

    balances = dict(data.client_opening if with_client else data.opening)
    for operation in data.operations:
        order = operation.order
        key = (
            (order.client_id, order.currency)
            if with_client
            else order.currency
        )
        balances[key] = balances.get(key, ZERO) + operation.amount
        operation_label, status_label = _operation_description(operation)
        payment = operation.payment
        description = (
            payment.note or "Поступление оплаты"
            if payment is not None
            else ", ".join(
                f"{item.product_label} × {item.quantity}"
                for item in order.items.all()
            )
        )
        colour = "#B42318" if operation.amount > 0 else "#067647"
        values = [
            _stamp(operation.occurred_at),
            operation_label,
            order.id,
            description,
            status_label,
            order.currency,
        ]
        if with_client:
            values.insert(1, order.client.name)
        cells = [Paragraph(_text(value), styles.body) for value in values]
        cells.append(
            Paragraph(
                f'<font color="{colour}">{_signed(operation.amount)}</font>',
                styles.right,
            )
        )
        cells.append(Paragraph(_money(balances[key]), styles.right))
        rows.append(cells)
    return rows


def _build(story, styles, title, subtitle, landscape_mode=True):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4) if landscape_mode else A4,
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
        Paragraph(_text(title), styles.h1),
        Paragraph(_text(subtitle), styles.small),
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
            Paragraph(_text(title), styles.h2),
            Spacer(1, 3 * mm),
        ]
    )


def _ledger_opening_block(styles, data: StatementData, *, with_client: bool):
    """Show the balances that seed the running ledger, even with no movements."""
    if with_client:
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
            _cells([name, currency, _money(opening)], styles, right=(2,))
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
                [currency, _money(data.opening.get(currency, ZERO))],
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
            _table(rows, widths, styles, aligns=aligns),
            Spacer(1, 5 * mm),
        ]
    )


def _client_summary(story, styles, data: StatementData) -> None:
    client = data.client
    if client is None:
        raise ValueError("Client statement data must contain a client")
    info = [
        ["Клиент", client.name, "Телефон", client.phone],
        ["ИИН / БИН", client.iin or "—", "Страна", client.country or "—"],
        ["Отделы", data.department_scope, "Период", data.period],
    ]
    story.extend(
        [
            _table(
                [
                    [
                        Paragraph(f"<b>{_text(row[0])}</b>", styles.small),
                        Paragraph(_text(row[1]), styles.body),
                        Paragraph(f"<b>{_text(row[2])}</b>", styles.small),
                        Paragraph(_text(row[3]), styles.body),
                    ]
                    for row in info
                ],
                [28 * mm, 95 * mm, 28 * mm, 70 * mm],
                styles,
                header=False,
            ),
            Spacer(1, 6 * mm),
        ]
    )
    for currency in data.currencies:
        totals = data.totals[currency]
        story.append(
            _reconciliation_block(
                styles,
                currency,
                data.opening.get(currency, ZERO),
                totals["sales"],
                totals["payments"],
            )
        )


def _client_orders(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Заказы")
    rows = [
        _cells(
            [
                "№",
                "Создан",
                "Статус",
                "Отгружен",
                "Отдел",
                "Транспорт",
                "Вал.",
                "Сумма",
                "Оплачено",
                "Долг",
            ],
            styles,
            right=(7, 8, 9),
        )
    ]
    for order in data.orders:
        shipment = getattr(order, "shipment", None)
        rows.append(
            _cells(
                [
                    order.id,
                    _stamp(order.created_at),
                    public_status_label(order.status),
                    _stamp(shipment.shipped_at) if shipment else "—",
                    department_name(data, order.department),
                    transport_label(order.transport_type),
                    order.currency,
                    _money(order.total_amount),
                    _money(order.paid_total),
                    _money(max(ZERO, order.remaining_amount)),
                ],
                styles,
                right=(7, 8, 9),
            )
        )
    story.append(
        _table(
            rows,
            [
                14 * mm,
                26 * mm,
                26 * mm,
                26 * mm,
                26 * mm,
                22 * mm,
                12 * mm,
                28 * mm,
                28 * mm,
                28 * mm,
            ],
            styles,
            aligns={7: "RIGHT", 8: "RIGHT", 9: "RIGHT"},
        )
    )


def _client_items(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Позиции заказов")
    rows = [
        _cells(
            ["Заказ", "Дата", "Товар", "Мешков", "Цена", "Сумма", "Вал."],
            styles,
            right=(3, 4, 5),
        )
    ]
    for order in data.orders:
        for item in order.items.all():
            rows.append(
                _cells(
                    [
                        order.id,
                        _stamp(order.created_at),
                        item.product_label,
                        item.quantity,
                        _money(item.unit_price),
                        _money(item.quantity * (item.unit_price or 0)),
                        order.currency,
                    ],
                    styles,
                    right=(3, 4, 5),
                )
            )
    story.append(
        _table(
            rows,
            [16 * mm, 28 * mm, 90 * mm, 22 * mm, 30 * mm, 34 * mm, 14 * mm],
            styles,
            aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"},
        )
    )


def _client_payments(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Платежи")
    rows = [
        _cells(
            [
                "№",
                "Дата",
                "Заказ",
                "Способ",
                "Статус",
                "Сумма",
                "Вал.",
                "Сотрудник",
            ],
            styles,
            right=(5,),
        )
    ]
    for payment in data.payments:
        author = payment.confirmed_by or payment.received_by or payment.recorded_by
        rows.append(
            _cells(
                [
                    payment.id,
                    _stamp(payment.confirmed_at or payment.paid_at),
                    payment.order_id,
                    payment_method_label(payment.method, archived_hint=True),
                    payment_status_label(payment.status),
                    _money(payment.amount),
                    payment.order.currency,
                    author.username if author else "—",
                ],
                styles,
                right=(5,),
            )
        )
    story.append(
        _table(
            rows,
            [
                16 * mm,
                28 * mm,
                16 * mm,
                34 * mm,
                30 * mm,
                32 * mm,
                12 * mm,
                30 * mm,
            ],
            styles,
            aligns={5: "RIGHT"},
        )
    )


def _client_debts(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Текущие долги")
    rows = [
        _cells(
            [
                "Заказ",
                "Отгружен",
                "Магазин",
                "Сумма",
                "Оплачено",
                "Остаток",
                "Вал.",
                "Способ",
            ],
            styles,
            right=(3, 4, 5),
        )
    ]
    for order in data.debt_orders:
        shipment = getattr(order, "shipment", None)
        rows.append(
            _cells(
                [
                    order.id,
                    _stamp(shipment.shipped_at if shipment else order.created_at),
                    order.store.name if order.store else "—",
                    _money(order.total_amount),
                    _money(order.paid_total),
                    _money(order.remaining_amount),
                    order.currency,
                    order_payment_method_label(order.payment_method),
                ],
                styles,
                right=(3, 4, 5),
            )
        )
    story.append(
        _table(
            rows,
            [
                16 * mm,
                28 * mm,
                40 * mm,
                30 * mm,
                30 * mm,
                30 * mm,
                12 * mm,
                30 * mm,
            ],
            styles,
            aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"},
        )
    )


def render_client_statement_pdf(data: StatementData) -> bytes:
    """Render a prepared single-client statement without querying the ORM."""
    if data.client is None:
        raise ValueError("Client statement data must contain a client")

    _register_fonts()
    styles = _Styles()
    story: list = []
    if "summary" in data.sections:
        _client_summary(story, styles, data)
    if "ledger" in data.sections:
        _start_section(story, styles, "Операции")
        story.append(_ledger_opening_block(styles, data, with_client=False))
        rows = _ledger_rows(styles, data, with_client=False)
        story.append(
            _table(
                rows,
                [
                    26 * mm,
                    30 * mm,
                    14 * mm,
                    78 * mm,
                    30 * mm,
                    12 * mm,
                    25 * mm,
                    25 * mm,
                ],
                styles,
                aligns={6: "RIGHT", 7: "RIGHT"},
            )
        )
    if "orders" in data.sections:
        _client_orders(story, styles, data)
    if "items" in data.sections:
        _client_items(story, styles, data)
    if "payments" in data.sections:
        _client_payments(story, styles, data)
    if "debts" in data.sections:
        _client_debts(story, styles, data)
    if not story:
        story = [Paragraph("Нет данных за выбранный период.", styles.body)]
    return _build(story, styles, "Выписка по клиенту", data.subtitle)


def _all_summary(story, styles, data: StatementData) -> None:
    story.extend(
        [
            _table(
                [
                    [
                        Paragraph("<b>Отделы</b>", styles.small),
                        Paragraph(_text(data.department_scope), styles.body),
                        Paragraph("<b>Период</b>", styles.small),
                        Paragraph(_text(data.period), styles.body),
                    ]
                ],
                [28 * mm, 95 * mm, 28 * mm, 70 * mm],
                styles,
                header=False,
            ),
            Spacer(1, 6 * mm),
        ]
    )
    for currency in data.currencies:
        totals = data.totals[currency]
        story.append(
            _reconciliation_block(
                styles,
                currency,
                data.opening.get(currency, ZERO),
                totals["sales"],
                totals["payments"],
            )
        )


def _all_clients(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Клиенты")
    rows = [
        _cells(
            [
                "Клиент",
                "Телефон",
                "Вал.",
                "Заказов",
                "Продажи",
                "Оплачено",
                "Долг",
            ],
            styles,
            right=(3, 4, 5, 6),
        )
    ]
    for client in data.clients:
        for currency in data.currencies:
            totals = data.client_totals.get((client.id, currency))
            if not totals:
                continue
            rows.append(
                _cells(
                    [
                        client.name,
                        client.phone,
                        currency,
                        totals["orders"],
                        _money(totals["sales"]),
                        _money(totals["payments"]),
                        _money(totals["debt"]),
                    ],
                    styles,
                    right=(3, 4, 5, 6),
                )
            )
    story.append(
        _table(
            rows,
            [64 * mm, 32 * mm, 12 * mm, 22 * mm, 34 * mm, 34 * mm, 34 * mm],
            styles,
            aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT", 6: "RIGHT"},
        )
    )


def _all_orders(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Все заказы")
    rows = [
        _cells(
            [
                "№",
                "Создан",
                "Клиент",
                "Статус",
                "Отдел",
                "Вал.",
                "Сумма",
                "Оплачено",
                "Долг",
            ],
            styles,
            right=(6, 7, 8),
        )
    ]
    for order in data.orders:
        rows.append(
            _cells(
                [
                    order.id,
                    _stamp(order.created_at),
                    order.client.name,
                    public_status_label(order.status),
                    department_name(data, order.department),
                    order.currency,
                    _money(order.total_amount),
                    _money(order.paid_total),
                    _money(max(ZERO, order.remaining_amount)),
                ],
                styles,
                right=(6, 7, 8),
            )
        )
    story.append(
        _table(
            rows,
            [
                14 * mm,
                26 * mm,
                50 * mm,
                26 * mm,
                26 * mm,
                12 * mm,
                28 * mm,
                28 * mm,
                26 * mm,
            ],
            styles,
            aligns={6: "RIGHT", 7: "RIGHT", 8: "RIGHT"},
        )
    )


def _all_items(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Позиции всех заказов")
    rows = [
        _cells(
            ["Заказ", "Клиент", "Товар", "Мешков", "Цена", "Сумма", "Вал."],
            styles,
            right=(3, 4, 5),
        )
    ]
    for order in data.orders:
        for item in order.items.all():
            rows.append(
                _cells(
                    [
                        order.id,
                        order.client.name,
                        item.product_label,
                        item.quantity,
                        _money(item.unit_price),
                        _money(item.quantity * (item.unit_price or 0)),
                        order.currency,
                    ],
                    styles,
                    right=(3, 4, 5),
                )
            )
    story.append(
        _table(
            rows,
            [16 * mm, 50 * mm, 74 * mm, 20 * mm, 28 * mm, 32 * mm, 14 * mm],
            styles,
            aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"},
        )
    )


def _all_payments(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Все платежи")
    rows = [
        _cells(
            [
                "№",
                "Дата",
                "Клиент",
                "Заказ",
                "Способ",
                "Статус",
                "Сумма",
                "Вал.",
            ],
            styles,
            right=(6,),
        )
    ]
    for payment in data.payments:
        rows.append(
            _cells(
                [
                    payment.id,
                    _stamp(payment.confirmed_at or payment.paid_at),
                    payment.order.client.name,
                    payment.order_id,
                    payment_method_label(payment.method, archived_hint=True),
                    payment_status_label(payment.status),
                    _money(payment.amount),
                    payment.order.currency,
                ],
                styles,
                right=(6,),
            )
        )
    story.append(
        _table(
            rows,
            [
                14 * mm,
                26 * mm,
                50 * mm,
                16 * mm,
                32 * mm,
                28 * mm,
                32 * mm,
                12 * mm,
            ],
            styles,
            aligns={6: "RIGHT"},
        )
    )


def _all_debts(story, styles, data: StatementData) -> None:
    _start_section(story, styles, "Текущие долги")
    rows = [
        _cells(
            [
                "Заказ",
                "Клиент",
                "Отгружен",
                "Сумма",
                "Оплачено",
                "Остаток",
                "Вал.",
            ],
            styles,
            right=(3, 4, 5),
        )
    ]
    for order in data.debt_orders:
        shipment = getattr(order, "shipment", None)
        rows.append(
            _cells(
                [
                    order.id,
                    order.client.name,
                    _stamp(shipment.shipped_at if shipment else order.created_at),
                    _money(order.total_amount),
                    _money(order.paid_total),
                    _money(order.remaining_amount),
                    order.currency,
                ],
                styles,
                right=(3, 4, 5),
            )
        )
    story.append(
        _table(
            rows,
            [16 * mm, 54 * mm, 28 * mm, 32 * mm, 32 * mm, 32 * mm, 14 * mm],
            styles,
            aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"},
        )
    )


def render_all_clients_statement_pdf(data: StatementData) -> bytes:
    """Render a prepared consolidated statement without querying the ORM."""
    if data.client is not None:
        raise ValueError("All-clients statement data must not contain a client")

    _register_fonts()
    styles = _Styles()
    story: list = []
    if "summary" in data.sections:
        _all_summary(story, styles, data)
    if "clients" in data.sections:
        _all_clients(story, styles, data)
    if "ledger" in data.sections:
        _start_section(story, styles, "Операции")
        story.append(_ledger_opening_block(styles, data, with_client=True))
        rows = _ledger_rows(styles, data, with_client=True)
        story.append(
            _table(
                rows,
                [
                    24 * mm,
                    40 * mm,
                    26 * mm,
                    13 * mm,
                    58 * mm,
                    26 * mm,
                    11 * mm,
                    24 * mm,
                    24 * mm,
                ],
                styles,
                aligns={7: "RIGHT", 8: "RIGHT"},
            )
        )
    if "orders" in data.sections:
        _all_orders(story, styles, data)
    if "items" in data.sections:
        _all_items(story, styles, data)
    if "payments" in data.sections:
        _all_payments(story, styles, data)
    if "debts" in data.sections:
        _all_debts(story, styles, data)
    if not story:
        story = [Paragraph("Нет данных за выбранный период.", styles.body)]
    return _build(story, styles, "Общая выписка по клиентам", data.subtitle)


def build_client_statement_pdf(
        client,
        date_from=None,
        date_to=None,
        departments=None,
        sections=None,
) -> bytes:
    """Build and render a PDF statement for one client."""
    data = build_statement_data(
        client=client,
        date_from=date_from,
        date_to=date_to,
        departments=departments,
        sections=sections,
    )
    return render_client_statement_pdf(data)


def build_all_clients_statement_pdf(
        date_from=None,
        date_to=None,
        departments=None,
        sections=None,
) -> bytes:
    """Build and render a consolidated PDF statement."""
    data = build_statement_data(
        date_from=date_from,
        date_to=date_to,
        departments=departments,
        sections=sections,
    )
    return render_all_clients_statement_pdf(data)
