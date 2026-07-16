"""Canonical formatting for decimal money values exposed by the API."""

from decimal import Decimal


MONEY_PLACES = Decimal("0.01")


def money_string(value) -> str:
    if value is None:
        value = Decimal("0")
    return str(Decimal(value).quantize(MONEY_PLACES))
