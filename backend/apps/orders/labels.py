PAYMENT_METHOD_LABELS = {
    "invoice": "Счёт на оплату",
    "kaspi": "Kaspi",
    "cash": "Наличные",
    "debt": "Долг",
    "card": "Карта",
}

ARCHIVED_PAYMENT_METHODS = frozenset({"card"})

PAYMENT_STATUS_LABELS = {
    "requested": "Запрошена",
    "received": "Получена",
    "confirmed": "Подтверждена",
    "rejected": "Отклонена",
}

TRANSPORT_LABELS = {"truck": "Трак", "train": "Вагон"}

# Order.payment_method — это выбор клиента по заказу, а не способ конкретной
# оплаты: у него есть собственное значение «смешанная».
ORDER_PAYMENT_METHOD_LABELS = {**PAYMENT_METHOD_LABELS, "mixed": "Смешанная"}


def order_payment_method_label(method: str) -> str:
    return ORDER_PAYMENT_METHOD_LABELS.get(method, method)


def payment_method_label(method: str, *, archived_hint: bool = False) -> str:
    """Подпись способа оплаты.

    ``archived_hint`` добавляет пометку «(архив)» — нужна в выписках, где
    встречаются оплаты способами, снятыми с использования.
    """
    label = PAYMENT_METHOD_LABELS.get(method, method)
    if archived_hint and method in ARCHIVED_PAYMENT_METHODS:
        return f"{label} (архив)"
    return label


def payment_status_label(status: str) -> str:
    return PAYMENT_STATUS_LABELS.get(status, status)


def transport_label(transport_type: str) -> str:
    return TRANSPORT_LABELS.get(transport_type, transport_type)
