"""Единственный источник правды по денежным итогам набора заказов.

Раньше формула долга (``total_amount - paid_total``) была скопирована в четыре
места, а часть копий клампила отрицательный остаток, часть — нет. Итог зависел
от того, через какой экран смотреть. Здесь правило записано один раз и
совпадает с :attr:`Order.is_debt`.

Второе правило — валюты не складываются. Заказ хранит собственный код валюты,
поэтому 1000 ₸ и 5 $ не дают «1005»: итог всегда возвращается разложенным по
валютам (``CurrencyTotals``), а вызывающий код показывает каждую отдельно.
"""

from collections import defaultdict
from decimal import Decimal

from apps.common.money import money_string

from .statuses import is_financial

ZERO = Decimal("0")

# Валюта, в которой считается заказ без явного кода. Совпадает с Order.currency.
DEFAULT_CURRENCY = "KZT"


def order_remaining(order) -> Decimal:
    """Непогашенный остаток заказа. Никогда не отрицательный.

    Переплата (возврат больше оплаты, ручная правка суммы) не должна уводить
    остаток в минус и вычитаться из долга по другим заказам.
    """
    return max(ZERO, order.remaining_amount)


def debt_orders(orders) -> list:
    """Заказы, формирующие дебиторку, по правилу :attr:`Order.is_debt`."""
    return [order for order in orders if order.is_debt]


def financial_orders(orders) -> list:
    """Заказы, участвующие в обороте: без черновиков, отказов и отмен."""
    return [order for order in orders if is_financial(order.status)]


def sum_by_currency(orders, amount) -> dict[str, Decimal]:
    """Сложить ``amount(order)`` отдельно по каждой валюте."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for order in orders:
        totals[order.currency or DEFAULT_CURRENCY] += amount(order)
    return dict(totals)


def debt_by_currency(orders) -> dict[str, Decimal]:
    """Дебиторка по валютам. Заказы фильтруются по :attr:`Order.is_debt`."""
    return sum_by_currency(debt_orders(orders), order_remaining)


def as_money_strings(totals: dict[str, Decimal]) -> dict[str, str]:
    return {currency: money_string(value) for currency, value in totals.items()}


def primary_currency(totals: dict[str, Decimal], fallback: str = DEFAULT_CURRENCY) -> str:
    """Валюта с наибольшим итогом — для сортировки и однострочных сводок."""
    if not totals:
        return fallback
    return max(totals.items(), key=lambda row: (row[1], row[0]))[0]
