"""Подписи и колонки выписки, общие для XLSX и PDF.

Деньги считает ``data.py``; здесь только то, как операции и итоги называются
в документе, чтобы два формата не расходились в словах и правилах.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Callable, Literal, NamedTuple

from apps.orders.labels import payment_method_label, refund_method_label
from apps.orders.statuses import public_status_label

from .data import StatementData, StatementOperation

ColumnKind = Literal["text", "number", "money", "signed", "date"]
Width = float | tuple[float | None, float | None]


@dataclass(frozen=True, slots=True)
class Column:
    """Колонка таблицы выписки.

    ``width`` — одна ширина для обеих выписок или пара «(выписка клиента,
    общая выписка)»; ``None`` в паре — колонки в этой выписке нет (например,
    «Клиент» есть только в общей). ``kind`` задаёт формат значения в ячейке.
    """

    header: str
    width: Width
    value: Callable[[Any], object]
    kind: ColumnKind = "text"


def columns_for(columns: list[Column], data: StatementData) -> list[Column]:
    """Колонки выписки клиента или общей выписки — с их шириной."""
    index = 0 if data.client is not None else 1
    picked = []
    for column in columns:
        width = column.width[index] if isinstance(column.width, tuple) else column.width
        if width is not None:
            picked.append(replace(column, width=width))
    return picked


def method_label(method: str) -> str:
    """В выписке отмечаем архивные способы, чтобы их не искали в кассе."""
    return payment_method_label(method, archived_hint=True)


def username(user) -> str:
    return user.username if user else "—"


def shipped_at(order):
    """Фактическая отгрузка заказа; ``None``, пока заказ не отгружен."""
    return getattr(getattr(order, "shipment", None), "shipped_at", None)


class OperationDisplay(NamedTuple):
    label: str
    description: str
    method: str
    author: Any


def operation_display(operation: StatementOperation) -> OperationDisplay:
    """Подписи одной строки ленты: операция, описание, способ/статус, автор."""
    order = operation.order
    if operation.kind == "sale":
        return OperationDisplay(
            "Продажа / отгрузка",
            ", ".join(
                f"{item.product_label} × {item.quantity}"
                for item in order.items.all()
            ),
            public_status_label(order.status),
            order.created_by,
        )
    if operation.kind == "refund":
        refund = operation.refund
        return OperationDisplay(
            "Возврат",
            refund.reason or "Возврат оплаты",
            refund_method_label(refund.method, archived_hint=True),
            refund.requested_by,
        )
    payment = operation.payment
    return OperationDisplay(
        "Оплата",
        payment.note or "Поступление оплаты",
        method_label(payment.method),
        payment.author,
    )


def reconciliation_lines(
    data: StatementData, currency: str,
) -> list[tuple[str, Decimal, str | None]]:
    """Блок сверки валюты: ``(подпись, сумма, роль)``, последняя строка — итог.

    Роль ``"debit"``/``"credit"`` означает знаковую сумму и её цвет: начисление
    наращивает долг, погашение его уменьшает. Итог обязан равняться сумме трёх.
    """
    totals = data.totals[currency]
    payment_movement = -totals["payments"]
    return [
        (f"Остаток на начало периода, {currency}", data.opening[currency], None),
        ("Начислено (отгрузки)", totals["sales"], "debit"),
        (
            "Оплачено (поступления − возвраты)",
            payment_movement,
            "credit" if payment_movement <= 0 else "debit",
        ),
        ("Остаток на конец периода", data.closing[currency], None),
    ]
