"""Сравнение названий, набранных людьми в разных раскладках.

Одно и то же слово приходит то кириллицей, то латиницей («ООО» и «OOO»,
«Д1с» и «Д1c»), с кавычками и лишними пробелами. Ключ сравнения снимает всё
это оформление; показывается человеку всегда исходный текст.
"""

import re
import unicodedata

from rest_framework.exceptions import ValidationError

# Кириллические буквы, которые на письме не отличить от латинских. Оператор
# с русской раскладкой печатает «А123ВС», а номер тот же, что и «A123BC».
CYRILLIC_TWINS = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
# Всё, кроме букв и цифр: пробелы (и неразрывные), кавычки, дефисы, точки.
_NOT_ALNUM = re.compile(r"[\W_]+")


def match_key(text) -> str:
    """Ключ точного сравнения: заглавные, двойники латиницей, только буквы и цифры."""
    value = unicodedata.normalize("NFKC", str(text or "")).upper().replace("Ё", "Е")
    return _NOT_ALNUM.sub("", value.translate(CYRILLIC_TWINS))


def dictionary_key(text, what: str, max_length: int) -> str:
    """Ключ словаря отчётов (код товара, название клиента) по введённому тексту.

    Пустой или не влезающий в колонку текст — ошибка ввода, а не 500.
    """
    key = match_key(text)
    if not key:
        raise ValidationError({"detail": f"Укажите {what} как в отчёте", "code": "alias_empty"})
    if max(len(key), len(" ".join(str(text).split()))) > max_length:
        raise ValidationError({
            "detail": f"{what.capitalize()} — не длиннее {max_length} символов",
            "code": "alias_too_long",
        })
    return key


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    """«1 вагон», «2 вагона», «12 вагонов» — форма слова по числу."""
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(tail % 10, many)


def group_digits(value) -> str:
    """«16 320», «122 400,5» — разряды через пробел, дробная часть через запятую."""
    whole, _, fraction = format(value, "f").partition(".")
    sign = "-" if whole.startswith("-") else ""
    grouped = f"{int(whole.lstrip('-') or 0):,}".replace(",", " ")
    fraction = fraction.rstrip("0")
    return sign + grouped + (f",{fraction}" if fraction else "")
