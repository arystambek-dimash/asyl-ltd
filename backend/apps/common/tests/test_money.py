from decimal import Decimal
from types import SimpleNamespace

from apps.common.money import (
    as_money_strings,
    primary_currency,
    sum_by_currency,
)


def test_sum_by_currency_never_mixes_nominal_amounts():
    items = [
        SimpleNamespace(currency="KZT", amount=Decimal("1000.00")),
        SimpleNamespace(currency="USD", amount=Decimal("5.00")),
        SimpleNamespace(currency="KZT", amount=Decimal("250.00")),
    ]

    totals = sum_by_currency(items, lambda item: item.amount)

    assert totals == {
        "KZT": Decimal("1250.00"),
        "USD": Decimal("5.00"),
    }
    assert as_money_strings(totals) == {
        "KZT": "1250.00",
        "USD": "5.00",
    }


def test_sum_by_currency_uses_default_for_missing_currency():
    item = SimpleNamespace(currency="", amount=Decimal("10.00"))

    assert sum_by_currency([item], lambda row: row.amount) == {
        "KZT": Decimal("10.00")
    }


def test_primary_currency_prefers_fallback_without_comparing_currencies():
    totals = {
        "KZT": Decimal("100.00"),
        "USD": Decimal("5000.00"),
    }

    assert primary_currency(totals) == "KZT"
    assert primary_currency(totals, fallback="USD") == "USD"
    assert primary_currency({}, fallback="USD") == "USD"
