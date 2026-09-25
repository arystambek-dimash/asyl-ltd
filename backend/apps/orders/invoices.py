from __future__ import annotations

from io import BytesIO

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.common.pdf import money_text, para_text, register_fonts

from .labels import payment_method_label, payment_status_label
from .models import Payment


def build_payment_receipt_pdf(payment: Payment) -> bytes:
    """Build an ASYL LTD payment statement for a confirmed payment."""
    register_fonts()
    supplier = settings.INVOICE_SUPPLIER
    payment = Payment.objects.select_related("order__client__user").get(pk=payment.pk)
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
    currency = payment.order.currency
    rows = [
        ["Номер квитанции", f"PAY-{payment.pk:06d}"],
        ["Заказ", f"№{payment.order_id}"],
        ["Плательщик", payment.order.client.name],
        ["Телефон", payment.order.client.phone or "—"],
        ["Способ оплаты", payment_method_label(payment.method)],
        ["Статус", payment_status_label(payment.status)],
        ["Дата", timezone.localtime(payment.recognized_at).strftime("%d.%m.%Y %H:%M")],
        ["Сумма", f"{money_text(payment.amount)} {currency}"],
        ["Возвращено", f"{money_text(payment.refunded_amount)} {currency}"],
        ["Итого после возврата", f"{money_text(payment.net_amount)} {currency}"],
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
             Paragraph(para_text(supplier["legal_name"]), normal)],
            [Paragraph("<b>БИН</b>", normal),
             Paragraph(para_text(supplier["bin"]), normal)],
            [Paragraph("<b>Банк / БИК</b>", normal),
             Paragraph(
                 f"{para_text(supplier['bank'])} / "
                 f"{para_text(supplier['bic'])}",
                 normal,
             )],
            [Paragraph("<b>ИИК</b>", normal),
             Paragraph(para_text(supplier["iban"]), normal)],
            [Paragraph("<b>Адрес</b>", normal),
             Paragraph(para_text(supplier["address"]), normal)],
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
            f"Выписка {para_text(supplier['short_name'])}", title
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
            f"{para_text(supplier['short_name'])}. "
            "Подлинность операции подтверждается записью в журнале платежей.",
            normal,
        ),
    ]
    doc.build(story)
    return buffer.getvalue()

