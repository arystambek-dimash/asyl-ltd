"""Debt rules that depend on the Order domain model and its statuses."""

from decimal import Decimal

from apps.common.money import sum_by_currency as _sum_by_currency

from .statuses import is_financial

_ZERO = Decimal("0")

# Долг — непогашенный остаток отгруженного заказа. Товар уехал — клиент должен,
# как бы он ни собирался платить (settlement_intent): «в долг», сразу или ещё
# не выбрал. Выборки кандидатов в долги сужаются этим статусом.
DEBT_STATUS = "shipped"


def order_remaining(order) -> Decimal:
    return max(_ZERO, order.remaining_amount)


def overpaid_amount(status: str, refundable: Decimal, total: Decimal) -> Decimal:
    """Переплата — сколько ещё вернуть клиенту.

    ``refundable`` — подтверждённые деньги заказа, которые ещё можно вернуть
    (:attr:`Payment.available_for_refund`: без завершённых и начатых возвратов):
    возврат по ссылке покупателю (Kaspi QR, ApiPay) ждёт его подтверждения, и
    второй раз его сумма к возврату не предлагается. ``total`` — сумма позиций.

    Подтверждённая оплата — неизменный факт: уменьшение количества или цены
    после предоплаты её не отменяет, излишек возвращают отдельной операцией
    (касса → «К возврату»). Заказ вне оборота (:func:`statuses.is_financial`:
    заявка, отказ, отмена) клиенту ничего не стоит — к возврату все его деньги
    (легаси до предоплаты, поздняя оплата старого Kaspi QR).
    """
    if not is_financial(status):
        return refundable
    return max(_ZERO, refundable - total)


def order_overpaid(order) -> Decimal:
    """Переплата заказа (:func:`overpaid_amount`); по выборке — ``querysets.order_overpaid_by_id``."""
    refundable = sum(
        (payment.available_for_refund for payment in order.payments.all()), _ZERO
    )
    return overpaid_amount(order.status, refundable, order.total_amount)


def debt_orders(orders) -> list:
    return [order for order in orders if order.is_debt]


def financial_orders(orders) -> list:
    return [order for order in orders if is_financial(order.status)]


def debt_by_currency(orders) -> dict[str, Decimal]:
    """Дебиторка по валютам. Заказы фильтруются по :attr:`Order.is_debt`."""
    return _sum_by_currency(debt_orders(orders), order_remaining)
