from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError

from apps.common.query_params import parse_money_param


@pytest.mark.parametrize("raw", [None, ""])
def test_parse_money_param_accepts_an_omitted_value(raw):
    assert parse_money_param(raw, "Сумма") is None


def test_parse_money_param_returns_a_decimal():
    assert parse_money_param("12.50", "Сумма") == Decimal("12.50")
    assert parse_money_param("0", "Сумма") == Decimal("0")


@pytest.mark.parametrize("raw", ["invalid", "NaN", "Infinity", "-Infinity"])
def test_parse_money_param_rejects_invalid_or_non_finite_values(raw):
    with pytest.raises(ValidationError) as error:
        parse_money_param(raw, "Сумма")

    assert error.value.detail["code"] == "bad_amount"


def test_parse_money_param_rejects_a_negative_value():
    with pytest.raises(ValidationError) as error:
        parse_money_param("-0.01", "Минимальный остаток")

    assert error.value.detail["code"] == "bad_amount"
