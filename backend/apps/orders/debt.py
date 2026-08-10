"""Debt rules that depend on the Order domain model and its statuses."""

from decimal import Decimal

from apps.common.money import sum_by_currency as _sum_by_currency

from .statuses import is_financial

_ZERO = Decimal("0")


def order_remaining(order) -> Decimal:
    return max(_ZERO, order.remaining_amount)


def debt_orders(orders) -> list:
    return [order for order in orders if order.is_debt]


def financial_orders(orders) -> list:
    return [order for order in orders if is_financial(order.status)]


def debt_by_currency(orders) -> dict[str, Decimal]:
    """Дебиторка по валютам. Заказы фильтруются по :attr:`Order.is_debt`."""
    return _sum_by_currency(debt_orders(orders), order_remaining)
