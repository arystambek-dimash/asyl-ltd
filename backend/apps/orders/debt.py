"""Debt rules that depend on the Order domain model and its statuses."""

from decimal import Decimal

from apps.common.money import (
    as_money_strings,
    money_string,
    primary_currency,
    sum_by_currency as _sum_by_currency,
)

from .statuses import is_financial

_ZERO = Decimal("0")

# Долг — непогашенный остаток отгруженного заказа. Товар уехал — клиент должен,
# как бы он ни собирался платить (settlement_intent): «в долг», сразу или ещё
# не выбрал. Выборки кандидатов в долги сужаются этим статусом.
DEBT_STATUS = "shipped"


def order_remaining(order) -> Decimal:
    return max(_ZERO, order.remaining_amount)


def counts_as_debt(status: str, total: Decimal, paid: Decimal) -> bool:
    """Правило :attr:`Order.is_debt` по сумме и подтверждённым деньгам.

    Для агрегатов по выборке, где объекта заказа нет.
    """
    return status == DEBT_STATUS and total > paid


def confirmed_and_reserved(payments) -> tuple[Decimal, Decimal]:
    """Подтверждённые деньги и резерв оплат в работе.

    Подтверждённые считаются нетто (:attr:`Payment.net_amount`, как
    :attr:`Order.paid_total`), резерв — полной суммой запрошенных и принятых
    оплат (``Payment.IN_PROGRESS_STATUSES``): их ещё могут подтвердить.
    ``payments`` — оплаты одного заказа: из prefetch или под блокировкой.
    """
    from .models import Payment

    confirmed = reserved = _ZERO
    for payment in payments:
        if payment.status == "confirmed":
            confirmed += payment.net_amount
        elif payment.status in Payment.IN_PROGRESS_STATUSES:
            reserved += payment.amount
    return confirmed, reserved


def available_to_pay(order, payments=None) -> Decimal:
    """Свободный остаток к оплате: сумма заказа без подтверждённого и резерва.

    ``payments`` — оплаты заказа под блокировкой; по умолчанию
    ``order.payments.all()`` (prefetch).
    """
    confirmed, reserved = confirmed_and_reserved(
        order.payments.all() if payments is None else payments
    )
    return max(_ZERO, order.total_amount - confirmed - reserved)


def payment_status(total: Decimal, paid: Decimal) -> str:
    """Статус оплаты (``Order.payment_status``) по сумме и подтверждённым деньгам.

    Заказ с нулевой суммой не «оплачен»: без цен платить не за что.
    """
    if paid <= 0:
        return "unpaid"
    if total > 0 and paid >= total:
        return "settled"
    return "partial"


def order_payment_status(order) -> str:
    """Статус оплаты заказа по факту денег (:func:`payment_status`)."""
    return payment_status(order.total_amount, order.paid_total)


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


def payment_counts_as_paid(payment) -> bool:
    """Входит ли оплата в «Оплачено» клиента — то же правило, что у
    ``paid_total`` финансовых заказов: подтверждена и заказ в обороте.
    Засчитывается нетто (:attr:`Payment.net_amount`, за вычетом возвратов)."""
    return payment.status == "confirmed" and is_financial(payment.order.status)


def debt_by_currency(orders) -> dict[str, Decimal]:
    """Дебиторка по валютам. Заказы фильтруются по :attr:`Order.is_debt`."""
    return _sum_by_currency(debt_orders(orders), order_remaining)


def debt_fields(totals: dict[str, Decimal], fallback: str) -> dict:
    """Поля долга в ответе API из раскладки остатков по валютам.

    ``debt_total`` — остаток в основной валюте (:func:`primary_currency` с
    валютой клиента ``fallback``), полная раскладка — в ``debt_by_currency``.
    """
    currency = primary_currency(totals, fallback=fallback)
    return {
        "debt_total": money_string(totals.get(currency, _ZERO)),
        "debt_currency": currency,
        "debt_by_currency": as_money_strings(totals),
    }
