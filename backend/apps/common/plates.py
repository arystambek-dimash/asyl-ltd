"""Номера машин (тягач и полуприцеп): единственный модуль правил.

Номер хранится слитно, латиницей и заглавными (07KG695ADT) — так его вводят
форма заказа, грузчик и портал, и так по нему ищут. Страну по номеру
определяем только для отображения: формат, не похожий на известный, — это
предупреждение, а не ошибка (у клиента из другой страны номер бывает любой).
Жёстко отклоняется только то, что номером быть не может.

Регулярки камер и автовесов зерна живут отдельно и сюда НЕ переезжают:
автоматика зерна распознаёт только казахстанские номера и менять её поведение
этим модулем нельзя.
"""

import re

from rest_framework.exceptions import ValidationError

from .text import CYRILLIC_TWINS as _CYRILLIC_TWINS
# Пробелы (в том числе неразрывные), дефисы, точки и «·» — только оформление.
_SEPARATORS = re.compile(r"[\s\-.·]+")
_PLATE_RE = re.compile(r"[0-9A-Z]{4,12}")

# Известные форматы: страна → регулярки с группами для красивого вывода.
_RU_LETTERS = "[ABEKMHOPCTYX]"
_FORMATS = (
    ("KZ", re.compile(r"(\d{3})([A-Z]{3})(\d{2})")),
    ("KZ", re.compile(r"(\d{3})([A-Z]{2})(\d{2})")),
    ("KZ", re.compile(r"([A-Z])(\d{3})([A-Z]{2,3})")),
    ("KG", re.compile(r"(\d{2})(KG)(\d{3})([A-Z]{2,3})")),
    ("KG", re.compile(r"([A-Z])(\d{4})([A-Z]{1,2})")),
    ("UZ", re.compile(r"(\d{2})([A-Z])(\d{3})([A-Z]{2})")),
    ("UZ", re.compile(r"(\d{2})(\d{3})([A-Z]{3})")),
    ("RU", re.compile(rf"({_RU_LETTERS})(\d{{3}})({_RU_LETTERS}{{2}})(\d{{2,3}})")),
    ("RU", re.compile(rf"({_RU_LETTERS}{{2}})(\d{{4}})(\d{{2,3}})")),
    # Киргизский номер, набранный без «KG» (07695ADT). Под него же подходит
    # номер юрлица Узбекистана — такой номер страну не показывает.
    ("KG", re.compile(r"(\d{2})(\d{3})([A-Z]{2,3})")),
)
_KG_INSERT = re.compile(r"^(\d{2})KG(?=\d)")
# Начало киргизского номера без «KG»: регион и три цифры, буквы — по желанию
# (поиск по «07695» находит 07KG695ADT).
_KG_MISSING = re.compile(r"^(\d{2})(\d{3}[A-Z]{0,3})$")


def normalize_plate(raw) -> str:
    """Слитная запись номера: заглавные латинские буквы и цифры.

    Чистая функция без страны: «KG» не вставляет и не удаляет, O и 0 не
    путает — только снимает оформление и кириллицу-двойники.
    """
    text = str(raw or "").strip().upper().translate(_CYRILLIC_TWINS)
    return _SEPARATORS.sub("", text)


def plate_match_key(compact: str) -> str:
    """Ключ сравнения двух записей одного номера.

    Срезает вставку «KG» после двух цифр региона (07KG695ADT и 07695ADT — одна
    машина), а префикс «KZ» и суффиксы «UZ»/«RUS» — только когда это отметка
    страны, см. :func:`_strip_country_marker`.
    """
    key = normalize_plate(compact)
    if not _matches(key):
        key = _strip_country_marker(key)
    return _KG_INSERT.sub(r"\1", key)


# Отметка страны рядом с номером: «KZ» спереди, «UZ»/«RUS» сзади.
_COUNTRY_MARKERS = (("KZ", "", "KZ"), ("", "RUS", "RU"), ("", "UZ", "UZ"))


def _strip_country_marker(compact: str) -> str:
    """Снять отметку страны, только если без неё остаётся номер этой страны.

    Номер известного формата отметки не несёт: A123RUS — старый казахстанский,
    B1234UZ — старый киргизский, а не «A123» и «B1234» с отметкой страны.
    """
    for prefix, suffix, country in _COUNTRY_MARKERS:
        if compact.startswith(prefix) and compact.endswith(suffix):
            rest = compact[len(prefix): len(compact) - len(suffix)]
            if any(found == country for found, _ in _matches(rest)):
                return rest
    return compact


def _matches(compact: str) -> list[tuple[str, re.Match]]:
    return [
        (country, match)
        for country, pattern in _FORMATS
        if (match := pattern.fullmatch(compact))
    ]


def is_known_plate(compact: str) -> bool:
    """Номер подходит под один из известных форматов KZ, KG, UZ, RU."""
    return bool(_matches(normalize_plate(compact)))


def is_valid_plate(raw) -> bool:
    """Проходит ли номер жёсткое правило: 4–12 латинских букв и цифр."""
    return bool(_PLATE_RE.fullmatch(normalize_plate(raw)))


def clean_plate(raw, *, field: str = "truck_number") -> str:
    """Нормализовать номер и отклонить то, что номером быть не может (400).

    Пустое значение — «номера нет».
    """
    compact = normalize_plate(raw)
    if compact and not is_valid_plate(compact):
        raise ValidationError({field: "Номер: от 4 до 12 латинских букв и цифр"})
    return compact


def format_plate(value: str) -> str:
    """Номер для людей: «07 KG 695 ADT». Незнакомый текст выводится как есть."""
    compact = normalize_plate(value)
    matches = _matches(compact)
    if not matches:
        return str(value or "").strip()
    return " ".join(matches[0][1].groups())


def format_plate_pair(truck: str, trailer: str = "", *, joiner: str = " / ") -> str:
    """Тягач и прицеп одной строкой: «07 KG 695 ADT / 07 KG 837 PB»."""
    truck_text = format_plate(truck)
    if not trailer:
        return truck_text
    return f"{truck_text or '—'}{joiner}{format_plate(trailer)}"


def plate_search_variants(search: str) -> list[str]:
    """Как ещё может быть записан искомый номер: слитно и с/без «KG»."""
    compact = normalize_plate(search)
    if not compact:
        return []
    variants = [compact]
    without_kg = _KG_INSERT.sub(r"\1", compact)
    if without_kg != compact:
        variants.append(without_kg)
    elif match := _KG_MISSING.match(compact):
        variants.append(f"{match.group(1)}KG{match.group(2)}")
    return variants
