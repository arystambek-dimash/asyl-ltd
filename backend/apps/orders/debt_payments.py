"""«Внести оплату» по клиенту: сумма гасит долговые заказы от старого к новому.

Одно внесение — обычные оплаты заказов (:func:`services.record_staff_payment`),
по одной на заказ, с общей пометкой, и одна сводная запись журнала
``debt_payment``. Своей модели нет: касса, выписки, статус оплаты и отчёты
видят те же оплаты, что и после «Принять оплату» в заказе. Валюты не
смешиваются — одно внесение в одной валюте. Корзина не участвует.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.clients.services import (
    is_payment_window_open,
    lock_client_orders,
    lock_scoped_client,
)
from apps.common.money import CURRENCY_SIGNS, ZERO, money_string
from apps.common.text import group_digits, plural_ru
from apps.eventlog.services import log_event

from .debt import (
    DEBT_STATUS,
    available_to_pay,
    debt_orders,
    oldest_debt_first,
    order_remaining,
)
from .labels import payment_method_label
from .models import Order, Payment
from .services import (
    _positive_money,
    assert_payment_method_allowed,
    record_staff_payment,
)

# Почему заказ пропущен при распределении: код — для фронта, текст — кассиру.
_SKIP_DETAILS = {
    "payment_window_closed": "не день оплаты по графику магазина",
    "payment_in_progress": "оплата уже в процессе",
}


def _skipped(order, reason: str) -> dict:
    return {"order": order, "reason": reason, "detail": _SKIP_DETAILS[reason]}


def plan_debt_payment(orders, amount: Decimal | None, *, today) -> dict:
    """Распределить сумму по долговым заказам клиента — без записи.

    ``orders`` — долговые заказы клиента одной валюты, уже отсортированные
    :func:`debt.oldest_debt_first`, с ``store`` и предзагруженными
    ``payments`` и ``items``. Заказ магазина вне дня оплаты по графику и заказ,
    чей остаток целиком занят оплатой в работе, пропускаются: деньги идут на
    следующие. Доля заказа не больше свободного остатка
    (:func:`debt.available_to_pay`). ``amount=None`` — план на весь свободный
    остаток (кнопка «Весь долг»). Сумму проверяет вызывающий.
    """
    payable = []
    skipped = []
    for order in orders:
        # Долговой заказ всегда отгружен, а окно магазина — график погашения долга.
        if order.store_id is not None and not is_payment_window_open(order.store, today):
            skipped.append(_skipped(order, "payment_window_closed"))
            continue
        cap = available_to_pay(order)
        if cap <= 0:
            skipped.append(_skipped(order, "payment_in_progress"))
            continue
        payable.append((order, cap))
    total_available = sum((cap for _order, cap in payable), ZERO)
    target = total_available if amount is None else amount
    left = target
    slices = []
    for order, cap in payable:
        if left <= 0:
            break
        share = min(left, cap)
        remaining_before = order_remaining(order)
        remaining_after = remaining_before - share
        slices.append({
            "order": order,
            "amount": share,
            "remaining_before": remaining_before,
            "remaining_after": remaining_after,
            "closes": remaining_after <= 0,
        })
        left -= share
    return {
        "amount": target,
        "total_available": total_available,
        "slices": slices,
        "skipped": skipped,
    }


def _money_text(value: Decimal, currency: str) -> str:
    """«3 150 000 ₸» — сумма в тексте для человека."""
    return f"{group_digits(value)} {CURRENCY_SIGNS.get(currency, currency)}"


def _method_text(method: str) -> str:
    """Способ внутри фразы: «наличные», «удалённая оплата»; «QR» остаётся как есть."""
    label = payment_method_label(method)
    return label if label.isupper() else label.lower()


def _shipped_at(order) -> str | None:
    shipped_at = getattr(getattr(order, "shipment", None), "shipped_at", None)
    return timezone.localtime(shipped_at).isoformat() if shipped_at else None


def _debt_candidates(client_pk: int, currency: str) -> list:
    """Долговые заказы клиента в валюте, от старого к новому. Корзины нет: ``Order.objects``."""
    orders = (
        Order.objects.filter(client_id=client_pk, status=DEBT_STATUS, currency=currency)
        .select_related("store", "shipment")
        .prefetch_related("payments", "items")
    )
    return sorted(debt_orders(orders), key=oldest_debt_first)


def _record_slices(client, plan: dict, user, *, method: str, currency: str) -> dict[int, int]:
    """Провести доли обычными оплатами заказов и записать одно сводное событие.

    Возвращает ``{order_id: payment_id}``. Ошибка любой доли откатывает всё
    внесение вместе с уже проведёнными долями (транзакция вызывающего).
    """
    slices = plan["slices"]
    count = len(slices)
    summary = (
        f"{_money_text(plan['amount'], currency)}, {_method_text(method)} "
        f"({count} {plural_ru(count, 'заказ', 'заказа', 'заказов')})"
    )
    common_note = f"Внесение оплаты: {summary}"
    payment_ids = {}
    for share in slices:
        payment = record_staff_payment(share["order"], share["amount"], user, method=method, note=common_note)
        payment_ids[share["order"].pk] = payment.pk
    # Не «payment»: журнал кассы и сводки по дням читают события оплат заказов,
    # а они уже записаны — по одному на каждую долю.
    log_event(
        "debt_payment",
        f"Внесение оплаты клиента «{client.display_name}»: {summary}",
        user=user,
        payload={
            "client_id": client.pk,
            "department": client.department.code if client.department_id else None,
            "currency": currency,
            "method": method,
            "amount": money_string(plan["amount"]),
            "slices": [
                {
                    "order_id": share["order"].pk,
                    "payment_id": payment_ids[share["order"].pk],
                    "amount": money_string(share["amount"]),
                }
                for share in slices
            ],
        },
    )
    return payment_ids


def _plan_payload(plan: dict, *, currency: str, method: str | None, payment_ids: dict[int, int]) -> dict:
    """План в ответе API: деньги строками, заказы номерами; ``method=None`` — предпросмотр."""
    return {
        "currency": currency,
        "method": method,
        "amount": money_string(plan["amount"]),
        "total_available": money_string(plan["total_available"]),
        "slices": [
            {
                "order_id": share["order"].pk,
                "shipped_at": _shipped_at(share["order"]),
                "remaining_before": money_string(share["remaining_before"]),
                "amount": money_string(share["amount"]),
                "remaining_after": money_string(share["remaining_after"]),
                "closes": share["closes"],
                "payment_id": payment_ids.get(share["order"].pk),
            }
            for share in plan["slices"]
        ],
        "skipped": [
            {"order_id": row["order"].pk, "reason": row["reason"], "detail": row["detail"]}
            for row in plan["skipped"]
        ],
    }


@transaction.atomic
def record_client_debt_payment(
    client, amount, user, *, method: str | None = None, currency: str, preview: bool = False,
) -> dict:
    """Внести оплату клиента: погасить его долг в ``currency`` от старого заказа к новому.

    ``preview`` — только план, без блокировок и записи; ``amount=None``
    допустим лишь в нём («Весь долг»). Способ в предпросмотре не участвует:
    касса спрашивает его после «Подтвердить», в ответе ``method`` — ``None``.
    Больше свободного остатка не принимаем: аванса у клиента нет. Без
    ``preview`` способ обязателен; заказы клиента и клиент под
    блокировкой (порядок как у удаления клиента), область отдела сотрудника
    (:func:`clients.services.lock_scoped_client`), затем по доле на заказ через
    :func:`services.record_staff_payment` с общей пометкой и одно событие
    ``debt_payment`` без заказа.
    """
    if amount is not None or not preview:
        amount = _positive_money(
            amount,
            detail="Сумма оплаты должна быть положительным денежным значением",
            code="invalid_amount",
        )
    if preview:
        method = None
    else:
        if method not in Payment.SETTLED_ON_RECORD:
            raise ValidationError({
                "detail": "Внести оплату можно наличными, Kaspi-терминалом или удалённо",
                "code": "debt_payment_method",
            })
        assert_payment_method_allowed(currency, method)
        lock_client_orders(client.pk)
        client = lock_scoped_client(client.pk, user)
    orders = _debt_candidates(client.pk, currency)
    if not orders:
        raise ValidationError({"detail": f"У клиента нет долга в {currency}", "code": "no_debt"})
    plan = plan_debt_payment(orders, amount, today=timezone.localdate())
    if plan["amount"] > plan["total_available"]:
        raise ValidationError({
            "detail": f"Максимум к оплате {_money_text(plan['total_available'], currency)}",
            "code": "amount_exceeds_debt",
            "max_amount": money_string(plan["total_available"]),
        })
    payment_ids = {}
    if not preview:
        payment_ids = _record_slices(client, plan, user, method=method, currency=currency)
    return _plan_payload(plan, currency=currency, method=method, payment_ids=payment_ids)
