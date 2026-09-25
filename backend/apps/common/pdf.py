"""Общее для PDF-документов: шрифт с кириллицей, текст в разметке и суммы.

Квитанция об оплате, выписки клиента и накладная рисуются ReportLab одним
шрифтом «InvoiceSans» (DejaVu Sans в контейнере, Arial на macOS).
"""

from decimal import Decimal
from html import escape
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


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
    raise RuntimeError("Для генерации PDF не найден шрифт DejaVu Sans или Arial")


def register_fonts() -> None:
    """Зарегистрировать «InvoiceSans» и «InvoiceSans-Bold» (один раз на процесс)."""
    if "InvoiceSans" in pdfmetrics.getRegisteredFontNames():
        return
    regular, bold = _font_paths()
    pdfmetrics.registerFont(TTFont("InvoiceSans", regular))
    pdfmetrics.registerFont(TTFont("InvoiceSans-Bold", bold))


def para_text(value: object) -> str:
    """Текст для разметки ``Paragraph`` буквально; ``None`` — пустая строка."""
    return escape(str(value if value is not None else ""), quote=True)


def money_text(value) -> str:
    """«1 234 567.00» — разряды через пробел, всегда две цифры копеек."""
    return f"{Decimal(value or 0):,.2f}".replace(",", " ")
