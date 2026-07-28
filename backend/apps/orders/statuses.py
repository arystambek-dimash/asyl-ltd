"""Публичное представление статусов заказа.

Внутренние этапы остаются в модели и меняются бизнес-процессами
(подтверждение, пост погрузки, отгрузка) — в журнале они видны как события.
Ручное управление намеренно ограничено четырьмя понятными состояниями:
«На рассмотрении» (оператор проверяет остатки), «Ожидает загрузки»,
«Отгружено», «Отменён» (нет товара на складе). Ключ группы — реальный статус
модели, поэтому выбор в селекте пишется в заказ без дополнительного маппинга.
"""

PUBLIC_STATUS_GROUPS = {
    "draft": "pending",
    "pending": "pending",
    "confirmed": "confirmed",
    "arrived": "confirmed",
    "loading": "confirmed",
    "loaded": "shipped",
    "shipped": "shipped",
    "rejected": "cancelled",
    "cancelled": "cancelled",
}

PUBLIC_STATUS_LABELS = {
    "pending": "На рассмотрении",
    "confirmed": "Ожидает загрузки",
    "shipped": "Отгружено",
    "cancelled": "Отменён",
}

PUBLIC_MANUAL_STATUSES = tuple(PUBLIC_STATUS_LABELS)


# Заказ в этих статусах ещё (или уже) не является финансовым документом:
# черновик и «на рассмотрении» не подтверждены, отказ и отмена аннулированы.
# Ни один из них не входит в оборот, выручку и долги.
NON_FINANCIAL_STATUSES = frozenset({"draft", "pending", "rejected", "cancelled"})


def is_financial(status: str) -> bool:
    """Учитывается ли заказ в денежных итогах (оборот, выручка, долг)."""
    return status not in NON_FINANCIAL_STATUSES


def public_status_key(status: str) -> str:
    return PUBLIC_STATUS_GROUPS.get(status, status)


def statuses_in_group(group: str) -> list[str]:
    """Внутренние статусы, входящие в публичную группу (для фильтров списка)."""
    return [s for s, g in PUBLIC_STATUS_GROUPS.items() if g == group]


def public_status_label(status: str) -> str:
    key = public_status_key(status)
    return PUBLIC_STATUS_LABELS.get(key, status)
