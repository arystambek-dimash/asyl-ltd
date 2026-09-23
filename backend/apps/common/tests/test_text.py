from decimal import Decimal

import pytest

from apps.common.text import group_digits, plural_ru


@pytest.mark.parametrize(
    ("count", "word"),
    [(1, "вагон"), (2, "вагона"), (4, "вагона"), (5, "вагонов"), (11, "вагонов"), (12, "вагонов"),
     (21, "вагон"), (22, "вагона"), (111, "вагонов"), (0, "вагонов")],
)
def test_plural_ru(count, word):
    assert plural_ru(count, "вагон", "вагона", "вагонов") == word


@pytest.mark.parametrize(
    ("value", "text"),
    [(16320, "16 320"), (Decimal("122400.00"), "122 400"), (Decimal("122400.50"), "122 400,5"),
     (Decimal("816"), "816"), (0, "0"), (Decimal("-1500.25"), "-1 500,25"), (1234567, "1 234 567")],
)
def test_group_digits(value, text):
    assert group_digits(value) == text
