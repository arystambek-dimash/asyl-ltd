"""PDF-версия выписки клиента.

Тот же набор данных, что и в Excel, но свёрстанный под лист A4: печать и
отправка клиенту. Разделы и отделы выбираются теми же ключами, поэтому одна
галочка в интерфейсе управляет обоими форматами.

Данные берутся из :mod:`apps.clients.statements` — здесь только вёрстка.
Дублировать расчёт нетто, долга и сверки нельзя: разойдутся форматы.
"""

from decimal import Decimal
from html import escape
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

from apps.orders.invoices import _register_fonts
from apps.orders.labels import (
    order_payment_method_label, payment_method_label, payment_status_label,
    transport_label,
)
from apps.orders.statuses import public_status_label

from .statements import (
    ALL_CLIENT_SECTIONS, CLIENT_SECTIONS, _current_debt_orders,
    _department_context, _department_name, _empty_currency_totals,
    _ledger_currencies, _local, _operation_amount, _operations,
    _opening_balances, _orders_in_period, _payments_in_period, _period_label,
    _selected_sections, _statement_orders,
)

INK = colors.HexColor("#101828")
MUTED = colors.HexColor("#667085")
RULE = colors.HexColor("#E4E7EC")
BAND = colors.HexColor("#F9FAFB")
PANEL = colors.HexColor("#F2F4F7")
DEBIT = colors.HexColor("#B42318")
CREDIT = colors.HexColor("#067647")


def _text(value: object) -> str:
    """Динамический текст внутри разметки Paragraph — только литералом."""
    return escape(str(value if value is not None else ""), quote=True)


def _money(value) -> str:
    return f"{Decimal(value or 0):,.2f}".replace(",", " ")


def _signed(value) -> str:
    amount = Decimal(value or 0)
    return f"{'+' if amount > 0 else ''}{_money(amount)}"


def _stamp(value) -> str:
    local = _local(value)
    return local.strftime("%d.%m.%Y %H:%M") if local else "—"


class _Styles:
    def __init__(self):
        base = getSampleStyleSheet()
        self.body = ParagraphStyle(
            "StBody", parent=base["BodyText"], fontName="InvoiceSans",
            fontSize=8, leading=10, textColor=INK)
        self.bold = ParagraphStyle(
            "StBold", parent=self.body, fontName="InvoiceSans-Bold")
        self.small = ParagraphStyle(
            "StSmall", parent=self.body, fontSize=7, leading=8.5, textColor=MUTED)
        self.right = ParagraphStyle("StRight", parent=self.body, alignment=TA_RIGHT)
        self.right_bold = ParagraphStyle(
            "StRightBold", parent=self.bold, alignment=TA_RIGHT)
        self.h1 = ParagraphStyle(
            "StH1", parent=self.bold, fontSize=16, leading=19)
        self.h2 = ParagraphStyle(
            "StH2", parent=self.bold, fontSize=10.5, leading=13)


def _table(rows, widths, styles, *, aligns=None, header=True, total_row=False):
    """Таблица в общем стиле: серая шапка, чередование строк, тонкие линии."""
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
    """Блок сверки: вх. остаток + начислено − оплачено = исх. остаток."""
    closing = opening + charged - paid
    rows = [
        [Paragraph(f"Остаток на начало периода, {_text(currency)}", styles.body),
         Paragraph(_money(opening), styles.right)],
        [Paragraph("Начислено (отгрузки)", styles.body),
         Paragraph(f'<font color="#B42318">{_signed(charged)}</font>', styles.right)],
        [Paragraph("Оплачено (поступления)", styles.body),
         Paragraph(f'<font color="#067647">{_signed(-paid)}</font>', styles.right)],
        [Paragraph("Остаток на конец периода", styles.bold),
         Paragraph(_money(closing), styles.right_bold)],
    ]
    return KeepTogether([
        Paragraph(f"Краткое содержание операций · {_text(currency)}", styles.h2),
        Spacer(1, 2 * mm),
        _table(rows, [110 * mm, 40 * mm], styles, aligns={1: "RIGHT"}, header=False,
               total_row=True),
        Spacer(1, 5 * mm),
    ])


def _ledger_rows(styles, operations, opening, department_names, *, with_client):
    """Лента операций со знаковой суммой и текущим остатком."""
    header = ["Дата", "Операция", "Заказ", "Описание", "Способ / статус",
              "Вал.", "Сумма", "Остаток"]
    if with_client:
        header.insert(1, "Клиент")
    rows = [[Paragraph(f"<b>{_text(name)}</b>", styles.small) for name in header]]

    balances: dict = {}
    balances.update(opening)
    for stamp, kind, obj in operations:
        amount = _operation_amount(kind, obj)
        order = obj if kind == 0 else obj.order
        key = (order.client_id, order.currency) if with_client else order.currency
        balances[key] = balances.get(key, Decimal("0")) + amount
        if kind == 0:
            description = ", ".join(
                f"{item.product_label} × {item.quantity}" for item in order.items.all())
            operation, status = "Продажа / отгрузка", public_status_label(order.status)
        else:
            description = obj.note or "Поступление оплаты"
            operation = "Оплата"
            status = payment_method_label(obj.method, archived_hint=True)
        colour = "#B42318" if amount > 0 else "#067647"
        values = [
            _stamp(stamp), operation, order.id, description, status,
            order.currency,
        ]
        if with_client:
            values.insert(1, order.client.name)
        cells = [Paragraph(_text(value), styles.body) for value in values]
        cells.append(Paragraph(
            f'<font color="{colour}">{_signed(amount)}</font>', styles.right))
        cells.append(Paragraph(_money(balances[key]), styles.right))
        rows.append(cells)
    return rows


def _build(story, styles, title, subtitle, landscape_mode=True):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4) if landscape_mode else A4,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=title, author="ASYL LTD",
    )

    def _page(canvas, document):
        canvas.saveState()
        canvas.setFont("InvoiceSans", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(12 * mm, 7 * mm, title)
        canvas.drawRightString(
            document.pagesize[0] - 12 * mm, 7 * mm, f"Стр. {canvas.getPageNumber()}")
        canvas.restoreState()

    head = [
        Paragraph(_text(title), styles.h1),
        Paragraph(_text(subtitle), styles.small),
        HRFlowable(width="100%", thickness=1.2, color=INK,
                   spaceBefore=2 * mm, spaceAfter=4 * mm),
    ]
    doc.build(head + story, onFirstPage=_page, onLaterPages=_page)
    return buffer.getvalue()


def build_client_statement_pdf(
    client, date_from=None, date_to=None, departments=None, sections=None,
) -> bytes:
    """Выписка одного клиента в PDF — те же разделы, что и в Excel."""
    _register_fonts()
    styles = _Styles()
    chosen = _selected_sections(sections, CLIENT_SECTIONS)

    base_orders = _statement_orders(client=client, departments=departments)
    orders = list(_orders_in_period(base_orders, date_from, date_to))
    sales_orders = list(_orders_in_period(
        base_orders.filter(status="shipped"), date_from, date_to, sale_date=True))
    debt_orders = _current_debt_orders(base_orders)
    from apps.orders.models import Payment

    payments_qs = Payment.objects.filter(
        order__client=client, order__deleted_at__isnull=True,
    ).select_related("order", "recorded_by", "received_by", "confirmed_by")
    if departments is not None:
        payments_qs = payments_qs.filter(order__department__in=departments)
    payments = list(_payments_in_period(payments_qs, date_from, date_to))
    opening = _opening_balances(base_orders, payments_qs, date_from)

    period = _period_label(date_from, date_to)
    department_names, department_scope = _department_context(departments)
    subtitle = (
        f"{client.name} · {department_scope} · {period} · "
        f"сформировано {timezone.localtime():%d.%m.%Y %H:%M}"
    )

    totals: dict = {}
    for order in orders:
        totals.setdefault(order.currency, _empty_currency_totals())["orders"] += 1
    for order in sales_orders:
        totals.setdefault(order.currency, _empty_currency_totals())["sales"] += order.total_amount
    for order in debt_orders:
        totals.setdefault(order.currency, _empty_currency_totals())["debt"] += max(
            Decimal("0"), order.remaining_amount)
    for payment in payments:
        if payment.status == "confirmed":
            totals.setdefault(
                payment.order.currency, _empty_currency_totals(),
            )["payments"] += payment.net_amount

    currencies = _ledger_currencies(
        opening,
        {code: value["sales"] for code, value in totals.items()},
        {code: value["payments"] for code, value in totals.items()},
        {code: value["debt"] for code, value in totals.items()},
    )
    for currency in currencies:
        totals.setdefault(currency, _empty_currency_totals())

    story: list = []
    if "summary" in chosen:
        info = [
            ["Клиент", client.name, "Телефон", client.phone],
            ["ИИН / БИН", client.iin or "—", "Страна", client.country or "—"],
            ["Отделы", department_scope, "Период", period],
        ]
        story += [
            _table(
                [[Paragraph(f"<b>{_text(row[0])}</b>", styles.small),
                  Paragraph(_text(row[1]), styles.body),
                  Paragraph(f"<b>{_text(row[2])}</b>", styles.small),
                  Paragraph(_text(row[3]), styles.body)] for row in info],
                [28 * mm, 95 * mm, 28 * mm, 70 * mm], styles, header=False,
            ),
            Spacer(1, 6 * mm),
        ]
        for currency in currencies:
            story.append(_reconciliation_block(
                styles, currency, opening.get(currency, Decimal("0")),
                totals[currency]["sales"], totals[currency]["payments"]))

    if "ledger" in chosen:
        story += _section(styles, "Операции")
        rows = _ledger_rows(
            styles, _operations(sales_orders, payments), dict(opening),
            department_names, with_client=False)
        story.append(_table(
            rows, [26 * mm, 30 * mm, 14 * mm, 78 * mm, 30 * mm, 12 * mm, 25 * mm, 25 * mm],
            styles, aligns={6: "RIGHT", 7: "RIGHT"}))

    if "orders" in chosen:
        story += _section(styles, "Заказы")
        rows = [_cells(
            ["№", "Создан", "Статус", "Отгружен", "Отдел", "Транспорт",
             "Вал.", "Сумма", "Оплачено", "Долг"], styles, right=(7, 8, 9))]
        for order in orders:
            shipment = getattr(order, "shipment", None)
            rows.append(_cells([
                order.id, _stamp(order.created_at), public_status_label(order.status),
                _stamp(shipment.shipped_at) if shipment else "—",
                _department_name(department_names, order.department),
                transport_label(order.transport_type), order.currency,
                _money(order.total_amount), _money(order.paid_total),
                _money(max(Decimal("0"), order.remaining_amount)),
            ], styles, right=(7, 8, 9)))
        story.append(_table(
            rows, [14 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm, 22 * mm, 12 * mm,
                   28 * mm, 28 * mm, 28 * mm], styles,
            aligns={7: "RIGHT", 8: "RIGHT", 9: "RIGHT"}))

    if "items" in chosen:
        story += _section(styles, "Позиции заказов")
        rows = [_cells(
            ["Заказ", "Дата", "Товар", "Мешков", "Цена", "Сумма", "Вал."],
            styles, right=(3, 4, 5))]
        for order in orders:
            for item in order.items.all():
                rows.append(_cells([
                    order.id, _stamp(order.created_at), item.product_label,
                    item.quantity, _money(item.unit_price),
                    _money(item.quantity * (item.unit_price or 0)), order.currency,
                ], styles, right=(3, 4, 5)))
        story.append(_table(
            rows, [16 * mm, 28 * mm, 90 * mm, 22 * mm, 30 * mm, 34 * mm, 14 * mm],
            styles, aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"}))

    if "payments" in chosen:
        story += _section(styles, "Платежи")
        rows = [_cells(
            ["№", "Дата", "Заказ", "Способ", "Статус", "Сумма", "Вал.", "Сотрудник"],
            styles, right=(5,))]
        for payment in payments:
            author = payment.confirmed_by or payment.received_by or payment.recorded_by
            rows.append(_cells([
                payment.id, _stamp(payment.confirmed_at or payment.paid_at),
                payment.order_id, payment_method_label(payment.method, archived_hint=True),
                payment_status_label(payment.status), _money(payment.amount),
                payment.order.currency, author.username if author else "—",
            ], styles, right=(5,)))
        story.append(_table(
            rows, [16 * mm, 28 * mm, 16 * mm, 34 * mm, 30 * mm, 32 * mm, 12 * mm, 30 * mm],
            styles, aligns={5: "RIGHT"}))

    if "debts" in chosen:
        story += _section(styles, "Текущие долги")
        rows = [_cells(
            ["Заказ", "Отгружен", "Магазин", "Сумма", "Оплачено", "Остаток",
             "Вал.", "Способ"], styles, right=(3, 4, 5))]
        for order in debt_orders:
            shipment = getattr(order, "shipment", None)
            rows.append(_cells([
                order.id,
                _stamp(shipment.shipped_at if shipment else order.created_at),
                order.store.name if order.store else "—",
                _money(order.total_amount), _money(order.paid_total),
                _money(order.remaining_amount), order.currency,
                order_payment_method_label(order.payment_method),
            ], styles, right=(3, 4, 5)))
        story.append(_table(
            rows, [16 * mm, 28 * mm, 40 * mm, 30 * mm, 30 * mm, 30 * mm, 12 * mm, 30 * mm],
            styles, aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"}))

    if not story:
        story = [Paragraph("Нет данных за выбранный период.", styles.body)]
    return _build(story, styles, "Выписка по клиенту", subtitle)


def _section(styles, title):
    """Заголовок раздела с новой страницы — так листы не слипаются."""
    return [
        PageBreak(),
        Paragraph(_text(title), styles.h2),
        Spacer(1, 3 * mm),
    ]


def build_all_clients_statement_pdf(
    date_from=None, date_to=None, departments=None, sections=None,
) -> bytes:
    """Общая выписка по всем клиентам в PDF."""
    from apps.orders.models import Payment

    from .models import Client

    _register_fonts()
    styles = _Styles()
    chosen = _selected_sections(sections, ALL_CLIENT_SECTIONS)

    base_orders = _statement_orders(departments=departments)
    orders = list(_orders_in_period(base_orders, date_from, date_to))
    sales_orders = list(_orders_in_period(
        base_orders.filter(status="shipped"), date_from, date_to, sale_date=True))
    debt_orders = _current_debt_orders(base_orders)

    payments_qs = Payment.objects.filter(
        order__deleted_at__isnull=True,
    ).select_related("order", "order__client", "recorded_by", "received_by",
                     "confirmed_by")
    if departments is not None:
        payments_qs = payments_qs.filter(order__department__in=departments)
    payments = list(_payments_in_period(payments_qs, date_from, date_to))

    period = _period_label(date_from, date_to)
    department_names, department_scope = _department_context(departments)
    subtitle = (
        f"{department_scope} · {period} · "
        f"сформировано {timezone.localtime():%d.%m.%Y %H:%M}"
    )

    from .statements import _client_opening_balances

    client_opening = _client_opening_balances(base_orders, payments_qs, date_from)
    opening: dict = {}
    for (_, currency), value in client_opening.items():
        opening[currency] = opening.get(currency, Decimal("0")) + value

    totals: dict = {}
    client_totals: dict = {}
    for order in orders:
        totals.setdefault(order.currency, _empty_currency_totals())["orders"] += 1
        client_totals.setdefault(
            (order.client_id, order.currency), _empty_currency_totals())["orders"] += 1
    for order in sales_orders:
        totals.setdefault(order.currency, _empty_currency_totals())["sales"] += order.total_amount
        client_totals.setdefault(
            (order.client_id, order.currency),
            _empty_currency_totals())["sales"] += order.total_amount
    for order in debt_orders:
        remaining = max(Decimal("0"), order.remaining_amount)
        totals.setdefault(order.currency, _empty_currency_totals())["debt"] += remaining
        client_totals.setdefault(
            (order.client_id, order.currency),
            _empty_currency_totals())["debt"] += remaining
    for payment in payments:
        if payment.status != "confirmed":
            continue
        currency = payment.order.currency
        totals.setdefault(currency, _empty_currency_totals())["payments"] += payment.net_amount
        client_totals.setdefault(
            (payment.order.client_id, currency),
            _empty_currency_totals())["payments"] += payment.net_amount

    currencies = _ledger_currencies(
        opening,
        {code: value["sales"] for code, value in totals.items()},
        {code: value["payments"] for code, value in totals.items()},
        {code: value["debt"] for code, value in totals.items()},
    )
    for currency in currencies:
        totals.setdefault(currency, _empty_currency_totals())

    story: list = []
    if "summary" in chosen:
        story += [
            _table([[
                Paragraph("<b>Отделы</b>", styles.small),
                Paragraph(_text(department_scope), styles.body),
                Paragraph("<b>Период</b>", styles.small),
                Paragraph(_text(period), styles.body),
            ]], [28 * mm, 95 * mm, 28 * mm, 70 * mm], styles, header=False),
            Spacer(1, 6 * mm),
        ]
        for currency in currencies:
            story.append(_reconciliation_block(
                styles, currency, opening.get(currency, Decimal("0")),
                totals[currency]["sales"], totals[currency]["payments"]))

    if "clients" in chosen:
        relevant = {
            *(order.client_id for order in orders),
            *(order.client_id for order in sales_orders),
            *(order.client_id for order in debt_orders),
            *(payment.order.client_id for payment in payments),
        }
        clients = list(Client.objects.filter(id__in=relevant).order_by(
            "first_name", "last_name", "id"))
        story += _section(styles, "Клиенты")
        rows = [_cells(
            ["Клиент", "Телефон", "Вал.", "Заказов", "Продажи", "Оплачено", "Долг"],
            styles, right=(3, 4, 5, 6))]
        for row_client in clients:
            for currency in currencies:
                data = client_totals.get((row_client.id, currency))
                if not data:
                    continue
                rows.append(_cells([
                    row_client.name, row_client.phone, currency, data["orders"],
                    _money(data["sales"]), _money(data["payments"]),
                    _money(data["debt"]),
                ], styles, right=(3, 4, 5, 6)))
        story.append(_table(
            rows, [64 * mm, 32 * mm, 12 * mm, 22 * mm, 34 * mm, 34 * mm, 34 * mm],
            styles, aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT", 6: "RIGHT"}))

    if "ledger" in chosen:
        story += _section(styles, "Операции")
        rows = _ledger_rows(
            styles, _operations(sales_orders, payments), dict(client_opening),
            department_names, with_client=True)
        story.append(_table(
            rows, [24 * mm, 40 * mm, 26 * mm, 13 * mm, 58 * mm, 26 * mm, 11 * mm,
                   24 * mm, 24 * mm], styles, aligns={7: "RIGHT", 8: "RIGHT"}))

    if "orders" in chosen:
        story += _section(styles, "Все заказы")
        rows = [_cells(
            ["№", "Создан", "Клиент", "Статус", "Отдел", "Вал.", "Сумма",
             "Оплачено", "Долг"], styles, right=(6, 7, 8))]
        for order in orders:
            rows.append(_cells([
                order.id, _stamp(order.created_at), order.client.name,
                public_status_label(order.status),
                _department_name(department_names, order.department),
                order.currency, _money(order.total_amount),
                _money(order.paid_total),
                _money(max(Decimal("0"), order.remaining_amount)),
            ], styles, right=(6, 7, 8)))
        story.append(_table(
            rows, [14 * mm, 26 * mm, 50 * mm, 26 * mm, 26 * mm, 12 * mm, 28 * mm,
                   28 * mm, 26 * mm], styles,
            aligns={6: "RIGHT", 7: "RIGHT", 8: "RIGHT"}))

    if "items" in chosen:
        story += _section(styles, "Позиции всех заказов")
        rows = [_cells(
            ["Заказ", "Клиент", "Товар", "Мешков", "Цена", "Сумма", "Вал."],
            styles, right=(3, 4, 5))]
        for order in orders:
            for item in order.items.all():
                rows.append(_cells([
                    order.id, order.client.name, item.product_label, item.quantity,
                    _money(item.unit_price),
                    _money(item.quantity * (item.unit_price or 0)), order.currency,
                ], styles, right=(3, 4, 5)))
        story.append(_table(
            rows, [16 * mm, 50 * mm, 74 * mm, 20 * mm, 28 * mm, 32 * mm, 14 * mm],
            styles, aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"}))

    if "payments" in chosen:
        story += _section(styles, "Все платежи")
        rows = [_cells(
            ["№", "Дата", "Клиент", "Заказ", "Способ", "Статус", "Сумма", "Вал."],
            styles, right=(6,))]
        for payment in payments:
            rows.append(_cells([
                payment.id, _stamp(payment.confirmed_at or payment.paid_at),
                payment.order.client.name, payment.order_id,
                payment_method_label(payment.method, archived_hint=True),
                payment_status_label(payment.status), _money(payment.amount),
                payment.order.currency,
            ], styles, right=(6,)))
        story.append(_table(
            rows, [14 * mm, 26 * mm, 50 * mm, 16 * mm, 32 * mm, 28 * mm, 32 * mm,
                   12 * mm], styles, aligns={6: "RIGHT"}))

    if "debts" in chosen:
        story += _section(styles, "Текущие долги")
        rows = [_cells(
            ["Заказ", "Клиент", "Отгружен", "Сумма", "Оплачено", "Остаток", "Вал."],
            styles, right=(3, 4, 5))]
        for order in debt_orders:
            shipment = getattr(order, "shipment", None)
            rows.append(_cells([
                order.id, order.client.name,
                _stamp(shipment.shipped_at if shipment else order.created_at),
                _money(order.total_amount), _money(order.paid_total),
                _money(order.remaining_amount), order.currency,
            ], styles, right=(3, 4, 5)))
        story.append(_table(
            rows, [16 * mm, 54 * mm, 28 * mm, 32 * mm, 32 * mm, 32 * mm, 14 * mm],
            styles, aligns={3: "RIGHT", 4: "RIGHT", 5: "RIGHT"}))

    if not story:
        story = [Paragraph("Нет данных за выбранный период.", styles.body)]
    return _build(story, styles, "Общая выписка по клиентам", subtitle)
