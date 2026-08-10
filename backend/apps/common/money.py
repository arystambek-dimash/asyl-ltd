"""Currency-safe money aggregation and formatting shared across apps."""

from collections import defaultdict
from decimal import Decimal


MONEY_PLACES = Decimal("0.01")
ZERO = Decimal("0")
DEFAULT_CURRENCY = "KZT"


def money_string(value) -> str:
    if value is None:
        value = ZERO
    return str(Decimal(value).quantize(MONEY_PLACES))


def sum_by_currency(items, amount) -> dict[str, Decimal]:
    """Sum ``amount(item)`` without ever mixing nominal currencies."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for item in items:
        totals[item.currency or DEFAULT_CURRENCY] += amount(item)
    return dict(totals)


def as_money_strings(totals: dict[str, Decimal]) -> dict[str, str]:
    return {
        currency: money_string(value)
        for currency, value in totals.items()
    }


def primary_currency(
    totals: dict[str, Decimal],
    fallback: str = DEFAULT_CURRENCY,
) -> str:
    """Prefer the business currency; never compare unlike nominal amounts."""
    if totals.get(fallback, ZERO) != ZERO:
        return fallback
    available = sorted(
        currency
        for currency, value in totals.items()
        if value != ZERO
    )
    return available[0] if available else fallback
