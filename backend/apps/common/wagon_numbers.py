"""Номер грузового вагона: 8 цифр, последняя — контрольная.

Единственное место правила «8 цифр» и алгоритма контрольной цифры: заказы,
разбор отчёта о вагонах (:mod:`apps.bots.parsing`) и распознавание номеров
камерами отгрузки.
"""

import re

WAGON_NUMBER_LENGTH = 8
_WAGON_NUMBER_RE = re.compile(rf"[0-9]{{{WAGON_NUMBER_LENGTH}}}")


def is_wagon_number(number: str) -> bool:
    """Ровно 8 цифр — формат номера вагона, контрольная цифра не проверяется."""
    return _WAGON_NUMBER_RE.fullmatch(number) is not None


def wagon_check_digit_ok(number: str) -> bool:
    """8 цифр и контрольная цифра сходится: первые 7 цифр с весами 2, 1, 2, … —
    сумма цифр произведений дополняется последней цифрой до кратного 10."""
    if not is_wagon_number(number):
        return False
    products = (int(digit) * (2 if index % 2 == 0 else 1) for index, digit in enumerate(number[:-1]))
    total = sum(value // 10 + value % 10 for value in products)
    return int(number[-1]) == (-total) % 10
