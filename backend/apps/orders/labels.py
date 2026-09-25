# Единственный словарь подписей оплат: его берут выписки, чеки и API
# (method_label/status_label), фронт своих копий не держит. kaspi — «QR»:
# этим словом пользуется касса, на терминале сотрудник видит QR-код.
PAYMENT_METHOD_LABELS = {
    "invoice": "Счёт на оплату",
    "kaspi": "QR",
    "cash": "Наличные",
    "remote": "Удалённая оплата",
    "debt": "Долг",
    "card": "Карта",
}

ARCHIVED_PAYMENT_METHODS = frozenset({"card"})

PAYMENT_STATUS_LABELS = {
    "requested": "Ожидает",
    "received": "В кассе",
    "confirmed": "Оплачено",
    "rejected": "Отклонено",
}

# Состояния счёта платёжного провайдера (Payment.effective_status): журнал
# транзакций показывает их тем же столбцом, что и этапы кассы.
PROVIDER_STAGE_LABELS = {
    "awaiting_customer": "Ожидает клиента",
    "cancellation_pending": "Отмена в обработке",
    "payment_error": "Ошибка счёта",
    "refund_pending": "Возврат в обработке",
    "partially_refunded": "Частично возвращено",
    "refunded": "Возвращено",
}

# Возврат по счёту ApiPay и по Kaspi QR уходит через одного провайдера.
REFUND_METHOD_LABELS = {"apipay": "ApiPay", "apipay_qr": "ApiPay"}

TRANSPORT_LABELS = {"truck": "Трак", "train": "Вагон"}

# Order.payment_method — это выбор клиента по заказу, а не способ конкретной
# оплаты: у него есть собственные значения «не выбран» и «смешанная».
ORDER_PAYMENT_METHOD_LABELS = {
    **PAYMENT_METHOD_LABELS,
    "pending": "Способ не выбран",
    "mixed": "Смешанная",
}


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


def refund_method_label(method: str, *, archived_hint: bool = False) -> str:
    """Подпись способа возврата: провайдер ApiPay или способ кассы."""
    if method in REFUND_METHOD_LABELS:
        return REFUND_METHOD_LABELS[method]
    return payment_method_label(method, archived_hint=archived_hint)


def payment_status_label(status: str) -> str:
    return PAYMENT_STATUS_LABELS.get(status, status)


def payment_stage_label(stage: str) -> str:
    """Подпись effective_status: этап кассы или состояние счёта провайдера."""
    return PROVIDER_STAGE_LABELS.get(stage) or payment_status_label(stage)


def transport_label(transport_type: str) -> str:
    return TRANSPORT_LABELS.get(transport_type, transport_type)
