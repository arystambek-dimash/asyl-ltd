"""PDF-счёт для заказа клиентского портала."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from html import escape
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .models import Order, Payment


ONES = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
ONES_F = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
         "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
        "семьдесят", "восемьдесят", "девяносто"]
HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот",
            "семьсот", "восемьсот", "девятьсот"]
SCALES = [
    ("", "", "", False),
    ("тысяча", "тысячи", "тысяч", True),
    ("миллион", "миллиона", "миллионов", False),
    ("миллиард", "миллиарда", "миллиардов", False),
    ("триллион", "триллиона", "триллионов", False),
    ("квадриллион", "квадриллиона", "квадриллионов", False),
    ("квинтиллион", "квинтиллиона", "квинтиллионов", False),
]


def build_payment_receipt_pdf(payment: Payment) -> bytes:
    """Build an ASYL LTD payment statement for a confirmed payment."""
    _register_fonts()
    supplier = settings.INVOICE_SUPPLIER
    payment = Payment.objects.select_related("order__client", "recorded_by").get(
        pk=payment.pk
    )
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=24 * mm, leftMargin=24 * mm,
        topMargin=22 * mm, bottomMargin=22 * mm,
        title=f"Выписка {supplier['short_name']} PAY-{payment.pk:06d}",
        author=supplier["legal_name"],
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReceiptTitle", parent=styles["Title"], fontName="InvoiceSans-Bold",
        fontSize=20, leading=24, alignment=TA_CENTER,
    )
    normal = ParagraphStyle(
        "ReceiptNormal", parent=styles["BodyText"], fontName="InvoiceSans",
        fontSize=11, leading=17,
    )
    method = {
        "cash": "Наличные", "kaspi": "Kaspi Pay",
        "invoice": "Счёт на оплату", "card": "Карта",
    }.get(payment.method, payment.method)
    rows = [
        ["Номер квитанции", f"PAY-{payment.pk:06d}"],
        ["Заказ", f"№{payment.order_id}"],
        ["Плательщик", payment.order.client.name],
        ["Телефон", payment.order.client.phone or "—"],
        ["Способ оплаты", method],
        ["Статус", "Подтверждён" if payment.status == "confirmed" else payment.status],
        ["Дата", timezone.localtime(payment.confirmed_at or payment.paid_at).strftime("%d.%m.%Y %H:%M")],
        ["Сумма", f"{payment.amount:,.2f} {payment.order.currency}".replace(",", " ")],
        ["Возвращено", f"{payment.refunded_amount:,.2f} {payment.order.currency}".replace(",", " ")],
        ["Итого после возврата", f"{payment.net_amount:,.2f} {payment.order.currency}".replace(",", " ")],
    ]
    table = Table(rows, colWidths=[55 * mm, 90 * mm], hAlign="CENTER")
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "InvoiceSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#667085")),
        ("FONTNAME", (1, 0), (1, -1), "InvoiceSans-Bold"),
        ("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#D0D5DD")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    company = Table(
        [
            [Paragraph("<b>Получатель</b>", normal),
             Paragraph(_escape_paragraph_text(supplier["legal_name"]), normal)],
            [Paragraph("<b>БИН</b>", normal),
             Paragraph(_escape_paragraph_text(supplier["bin"]), normal)],
            [Paragraph("<b>Банк / БИК</b>", normal),
             Paragraph(
                 f"{_escape_paragraph_text(supplier['bank'])} / "
                 f"{_escape_paragraph_text(supplier['bic'])}",
                 normal,
             )],
            [Paragraph("<b>ИИК</b>", normal),
             Paragraph(_escape_paragraph_text(supplier["iban"]), normal)],
            [Paragraph("<b>Адрес</b>", normal),
             Paragraph(_escape_paragraph_text(supplier["address"]), normal)],
        ],
        colWidths=[42 * mm, 103 * mm],
    )
    company.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "InvoiceSans"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#D0D5DD")),
        ("INNERGRID", (0, 0), (-1, -1), .5, colors.HexColor("#E4E7EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story = [
        Paragraph(
            f"Выписка {_escape_paragraph_text(supplier['short_name'])}", title
        ),
        Spacer(1, 2 * mm),
        Paragraph("Квитанция о подтверждённой оплате", normal),
        Spacer(1, 7 * mm),
        company,
        Spacer(1, 7 * mm),
        table,
        Spacer(1, 8 * mm),
        Paragraph(
            f"Документ сформирован информационной системой "
            f"{_escape_paragraph_text(supplier['short_name'])}. "
            "Подлинность операции подтверждается записью в журнале платежей.",
            normal,
        ),
    ]
    doc.build(story)
    return buffer.getvalue()


def _escape_paragraph_text(value: object) -> str:
    """Render dynamic text literally inside ReportLab ``Paragraph`` markup."""
    return escape(str(value), quote=True)


def _plural(value: int, one: str, few: str, many: str) -> str:
    tail = value % 100
    if 11 <= tail <= 14:
        return many
    last = value % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _triplet_words(value: int, feminine: bool = False) -> list[str]:
    words: list[str] = []
    hundreds, rest = divmod(value, 100)
    tens, ones = divmod(rest, 10)
    if hundreds:
        words.append(HUNDREDS[hundreds])
    if tens == 1:
        words.append(TEENS[ones])
    else:
        if tens:
            words.append(TENS[tens])
        if ones:
            words.append((ONES_F if feminine else ONES)[ones])
    return words


def amount_in_words(amount: Decimal, currency_code: str = "KZT") -> str:
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole = int(amount)
    coins = int((amount - whole) * 100)
    if whole == 0:
        words = ["ноль"]
    else:
        words = []
        groups: list[int] = []
        value = whole
        while value:
            groups.append(value % 1000)
            value //= 1000
        for index in range(len(groups) - 1, -1, -1):
            group = groups[index]
            if not group:
                continue
            scale = SCALES[index]
            words.extend(_triplet_words(group, scale[3]))
            if index:
                words.append(_plural(group, scale[0], scale[1], scale[2]))
    if currency_code == "USD":
        currency = _plural(whole, "доллар", "доллара", "долларов")
        coin = _plural(coins, "цент", "цента", "центов")
        return f"{' '.join(words)} {currency} {coins:02d} {coin}"
    currency = _plural(whole, "тенге", "тенге", "тенге")
    return f"{' '.join(words)} {currency} {coins:02d} тиын"


def _font_paths() -> tuple[str, str]:
    candidates = [
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
         Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            return str(regular), str(bold)
    raise RuntimeError("Для генерации счета не найден шрифт DejaVu Sans или Arial")


def _register_fonts() -> None:
    if "InvoiceSans" in pdfmetrics.getRegisteredFontNames():
        return
    regular, bold = _font_paths()
    pdfmetrics.registerFont(TTFont("InvoiceSans", regular))
    pdfmetrics.registerFont(TTFont("InvoiceSans-Bold", bold))


def build_invoice_pdf(order: Order) -> bytes:
    """Сформировать PDF-счёт по подтверждённым ценам заказа."""
    _register_fonts()
    supplier = settings.INVOICE_SUPPLIER
    issued_on = timezone.localdate()
    total = order.total_amount.quantize(Decimal("0.01"))
    vat_rate = Decimal(str(supplier["vat_rate"]))
    vat = (total * vat_rate / (Decimal("100") + vat_rate)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=13 * mm, bottomMargin=13 * mm,
        title=f"Счет на оплату №{order.id} от {issued_on:%d.%m.%Y}",
        author=supplier["short_name"],
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("InvoiceBody", parent=styles["BodyText"], fontName="InvoiceSans",
                          fontSize=8.5, leading=11, textColor=colors.black)
    small = ParagraphStyle("InvoiceSmall", parent=body, fontSize=7.5, leading=9)
    bold = ParagraphStyle("InvoiceBold", parent=body, fontName="InvoiceSans-Bold")
    title = ParagraphStyle("InvoiceTitle", parent=bold, fontSize=15, leading=18)
    center = ParagraphStyle("InvoiceCenter", parent=bold, alignment=TA_CENTER)
    right = ParagraphStyle("InvoiceRight", parent=body, alignment=TA_RIGHT)
    right_bold = ParagraphStyle("InvoiceRightBold", parent=bold, alignment=TA_RIGHT)
    invoice_number = _escape_paragraph_text(order.id)
    issue_date = _escape_paragraph_text(f"{issued_on:%d.%m.%Y}")

    story = [
        Paragraph(
            "Внимание! Оплата данного счета означает согласие с условиями поставки товара.<br/>"
            "Уведомление об оплате обязательно, в противном случае не гарантируется наличие "
            "товара на складе. Товар отпускается по факту прихода денег на расчетный счет "
            "Поставщика, самовывозом, при наличии доверенности и документов, удостоверяющих личность.",
            small,
        ),
        Spacer(1, 5 * mm),
        Paragraph("Образец платежного поручения", bold),
    ]

    bank_data = [
        [Paragraph("<b>Бенефициар:</b><br/>" +
                   _escape_paragraph_text(supplier["legal_name"]) +
                   "<br/><br/>БИН: " + _escape_paragraph_text(supplier["bin"]), small),
         Paragraph("<b>ИИК</b><br/><br/>" +
                   _escape_paragraph_text(supplier["iban"]), center),
         Paragraph("<b>Кбе</b><br/><br/>" +
                   _escape_paragraph_text(supplier["kbe"]), center)],
        [Paragraph("<b>Банк бенефициара:</b><br/>" +
                   _escape_paragraph_text(supplier["bank"]), small),
         Paragraph("<b>БИК</b><br/>" + _escape_paragraph_text(supplier["bic"]), center),
         Paragraph("<b>Код назначения платежа</b><br/>" +
                   _escape_paragraph_text(supplier["payment_code"]), center)],
    ]
    bank = Table(bank_data, colWidths=[112 * mm, 38 * mm, 28 * mm],
                 rowHeights=[26 * mm, 15 * mm])
    bank.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story += [bank, Spacer(1, 8 * mm),
              Paragraph(f"Счет на оплату №{invoice_number} от {issue_date}", title),
              HRFlowable(width="100%", thickness=1.8, color=colors.black, spaceBefore=2 * mm,
                         spaceAfter=3 * mm)]

    buyer = order.client.company_name.strip() or order.client.name
    details = Table([
        [Paragraph("Поставщик:", body),
         Paragraph(f"<b>{_escape_paragraph_text(supplier['legal_name'])}</b>, " +
                   _escape_paragraph_text(supplier["address"]), body)],
        [Paragraph("Покупатель:", body),
         Paragraph("<b>ИИН/БИН: " + _escape_paragraph_text(order.client.iin) +
                   ", " + _escape_paragraph_text(buyer) + "</b>", body)],
        [Paragraph("Договор:", body), Paragraph("<b>Без договора</b>", body)],
    ], colWidths=[25 * mm, 153 * mm], rowHeights=[None, 16 * mm, 10 * mm])
    details.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(details)

    rows = [[Paragraph("№", center), Paragraph("Код", center),
             Paragraph("Наименование", center), Paragraph("Кол-во", center),
             Paragraph("Ед.", center), Paragraph("Цена", center), Paragraph("Сумма", center)]]
    for index, item in enumerate(order.items.all(), 1):
        # Подтверждение заказа требует договорную цену. Ноль защищает старые
        # незавершённые записи без цены от падения генератора документа.
        price = item.unit_price if item.unit_price is not None else Decimal("0")
        line_total = price * item.quantity
        rows.append([
            Paragraph(_escape_paragraph_text(index), center), "",
            Paragraph(_escape_paragraph_text(item.product_label), small),
            Paragraph(_escape_paragraph_text(item.quantity), center),
            Paragraph("меш.", center),
            Paragraph(_escape_paragraph_text(f"{price:,.2f}"), right),
            Paragraph(_escape_paragraph_text(f"{line_total:,.2f}"), right),
        ])
    items_table = Table(rows, repeatRows=1,
                        colWidths=[8 * mm, 18 * mm, 70 * mm, 17 * mm, 14 * mm, 24 * mm, 27 * mm])
    items_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.65, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F1F1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    totals = Table([
        [Paragraph("Итого:", right_bold),
         Paragraph(_escape_paragraph_text(f"{total:,.2f}"), right_bold)],
        [Paragraph("В том числе НДС:", right_bold),
         Paragraph(_escape_paragraph_text(f"{vat:,.2f}"), right_bold)],
    ], colWidths=[151 * mm, 27 * mm])
    totals.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    currency_code = order.currency
    words = amount_in_words(total, currency_code)
    ending = KeepTogether([
        totals,
        Spacer(1, 2 * mm),
        Paragraph("Всего наименований " + _escape_paragraph_text(len(rows) - 1) +
                  ", на сумму " + _escape_paragraph_text(f"{total:,.2f}") +
                  " " + _escape_paragraph_text(currency_code), body),
        Paragraph("<b>Всего к оплате: " + _escape_paragraph_text(words) + "</b>", body),
        HRFlowable(width="100%", thickness=1.8, color=colors.black,
                   spaceBefore=2 * mm, spaceAfter=5 * mm),
        Table([[Paragraph("<b>Исполнитель</b>", body), "", Paragraph("//", body)]],
              colWidths=[28 * mm, 80 * mm, 10 * mm],
              style=TableStyle([("LINEBELOW", (1, 0), (1, 0), 0.8, colors.black)])),
    ])
    story += [items_table, ending]
    doc.build(story)
    return buffer.getvalue()
