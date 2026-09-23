"""«Накладная на отпуск товаров» — печатная форма отгрузки грузчика.

Повторяет бумажный бланк мельницы: шапка с точкой, номер (= номер заказа),
дата и время, машина, покупатель, таблица товара с ценами и итог, подписи
из ``WaybillSettings``. Отгрузка по отчёту о вагонах — вместо номера машины
станция назначения и таблица вагонов.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.orders.invoices import _register_fonts
from apps.orders.models import Order
from apps.orders.transport import order_wagons, transport_number_text

from .models import WaybillSettings

CURRENCY_WORDS = {"KZT": "в тенге", "USD": "в долларах"}


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ").replace(".00", "")


def _kg(value: Decimal) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _local_time(moment) -> str:
    return f"{timezone.localtime(moment):%H:%M}" if moment else ""


_GRID_STYLE = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
    ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
])


def _wagons_table(wagons, width, *, head, cell, number, center) -> Table:
    """Вагоны отгрузки по отчёту: номер, товар, мешки и вес — с итогом."""
    rows = [[
        Paragraph("№<br/>п/п", head), Paragraph("№ вагона", head), Paragraph("Наименование продукции", head),
        Paragraph("Кол-во в мешках", head), Paragraph("Вес, кг", head),
    ]]
    for index, wagon in enumerate(wagons, 1):
        rows.append([
            Paragraph(str(index), center), Paragraph(_text(wagon.number), cell),
            Paragraph(_text(wagon.product_label), cell), Paragraph(str(wagon.bags), number),
            Paragraph(_kg(wagon.weight_kg), number),
        ])
    rows.append([
        "", Paragraph(f"Вагонов: {len(wagons)}", cell), "",
        Paragraph(str(sum(wagon.bags for wagon in wagons)), number),
        Paragraph(_kg(sum((wagon.weight_kg for wagon in wagons), Decimal("0"))), number),
    ])
    table = Table(
        rows, colWidths=[width * share for share in (0.06, 0.2, 0.42, 0.14, 0.18)], repeatRows=1,
    )
    # Строки плотнее товарных: партия в 12 вагонов с подписями — на одном листе A5.
    table.setStyle(_GRID_STYLE)
    table.setStyle(TableStyle([("TOPPADDING", (0, 1), (-1, -1), 1), ("BOTTOMPADDING", (0, 1), (-1, -1), 1)]))
    return table


def build_waybill_pdf(order: Order) -> bytes:
    _register_fonts()
    settings = WaybillSettings.load()
    shipment = getattr(order, "shipment", None)
    shipped_at = shipment.shipped_at if shipment else None
    issued_on = timezone.localtime(shipped_at).date() if shipped_at else timezone.localdate()

    base = ParagraphStyle("Waybill", fontName="InvoiceSans", fontSize=8.5, leading=11)
    bold = ParagraphStyle("WaybillBold", parent=base, fontName="InvoiceSans-Bold")
    title = ParagraphStyle("WaybillTitle", parent=bold, fontSize=12, leading=15)
    head = ParagraphStyle("WaybillHead", parent=bold, fontSize=7.5, leading=9, alignment=TA_CENTER)
    cell = ParagraphStyle("WaybillCell", parent=base, fontSize=8, leading=10)
    number = ParagraphStyle("WaybillNumber", parent=cell, alignment=TA_RIGHT)
    center = ParagraphStyle("WaybillCenter", parent=cell, alignment=TA_CENTER)
    right = ParagraphStyle("WaybillRight", parent=base, alignment=TA_RIGHT)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A5, leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=10 * mm, bottomMargin=10 * mm,
        title=f"Накладная №{order.pk} от {issued_on:%d.%m.%Y}",
    )
    width = A5[0] - 20 * mm

    header = Table(
        [[Paragraph("Накладная на отпуск товаров", title), Paragraph(_text(settings.point_name), right)]],
        colWidths=[width * 0.62, width * 0.38],
    )
    buyer = order.client.display_name
    wagons = order_wagons(order)
    if wagons:
        transport_line = f"Вагоны: {len(wagons)} — список ниже"
    else:
        transport_label = "№ Вагона:" if order.transport_type == "train" else "№ Автомашины:"
        transport_value = transport_number_text(order) or ("—" if order.transport_type == "truck" else "вагон")
        transport_line = f"{transport_label} {_text(transport_value)}"
    detail_rows = [
        [Paragraph(f"№ {order.pk}", ParagraphStyle("WaybillNumberTitle", parent=bold, fontSize=11, leading=14)), "", ""],
        [
            Paragraph(f"Дата: {issued_on:%d.%m.%Y}", base),
            Paragraph(f"Время входа: {_local_time(shipment.arrived_at if shipment else None)}", base),
            Paragraph(f"выхода: {_local_time(shipped_at)}", base),
        ],
        [Paragraph(transport_line, base), "", ""],
    ]
    if order.transport_type == "train" and order.rail_station:
        detail_rows.append([Paragraph(f"Станция назначения: {_text(order.rail_station)}", base), "", ""])
    detail_rows.append([Paragraph(f"Покупатель: {_text(buyer)}", base), "", ""])
    details = Table(detail_rows, colWidths=[width * 0.36, width * 0.36, width * 0.28])
    details.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)),
        *(("SPAN", (0, row), (-1, row)) for row in range(2, len(detail_rows))),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))

    currency_word = CURRENCY_WORDS.get(order.currency, order.currency)
    rows = [[
        Paragraph("№<br/>п/п", head), Paragraph("Наименование продукции", head),
        Paragraph("Кол-во в мешках", head), Paragraph("Кол-во в кг", head),
        Paragraph("Цена за мешок", head), Paragraph("Цена за кг", head),
        Paragraph(f"Стоимость {currency_word}", head),
    ]]
    total_bags = 0
    total_kg = Decimal("0")
    total_amount = Decimal("0")
    for index, item in enumerate(order.items.all(), 1):
        weight = Decimal(item.product_weight_kg or 0)
        kg = weight * item.quantity
        price = item.unit_price
        line_total = price * item.quantity if price is not None else None
        per_kg = (price / weight).quantize(Decimal("0.01")) if price is not None and weight else None
        total_bags += item.quantity
        total_kg += kg
        total_amount += line_total or Decimal("0")
        rows.append([
            Paragraph(str(index), center), Paragraph(_text(item.product_label), cell),
            Paragraph(str(item.quantity), number), Paragraph(_kg(kg), number),
            Paragraph(_money(price), number), Paragraph(_money(per_kg), number),
            Paragraph(_money(line_total), number),
        ])
    rows.append([
        "", Paragraph("ИТОГО:", cell), Paragraph(f"{total_bags}", number),
        Paragraph(f"{_kg(total_kg)}", number), "", "",
        Paragraph(f"{_money(total_amount)}", number),
    ])
    table = Table(
        rows,
        colWidths=[width * share for share in (0.06, 0.28, 0.12, 0.12, 0.14, 0.11, 0.17)],
        repeatRows=1,
    )
    table.setStyle(_GRID_STYLE)

    signer_rows = [
        [Paragraph(f"{_text(signer.get('role', ''))}:", base), "", Paragraph(_text(signer.get("name", "")), base)]
        for signer in settings.signers
        if signer.get("role") or signer.get("name")
    ]
    signer_rows.append([Paragraph("Получатель:", base), "", ""])
    signatures = Table(signer_rows, colWidths=[width * 0.24, width * 0.4, width * 0.36], rowHeights=9 * mm)
    signatures.setStyle(TableStyle([
        ("LINEBELOW", (1, 0), (1, -1), 0.6, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))

    story = [header, Spacer(1, 2 * mm), details, Spacer(1, 3 * mm), table]
    if wagons:
        story += [Spacer(1, 3 * mm), _wagons_table(wagons, width, head=head, cell=cell, number=number, center=center)]
    story += [Spacer(1, 4 * mm), signatures, Spacer(1, 3 * mm), Paragraph("М.П.", right)]
    doc.build(story)
    return buffer.getvalue()
