PUBLIC_STATUS_GROUPS = {
    "draft": "pending",
    "pending": "pending",
    "confirmed": "confirmed",
    "arrived": "confirmed",
    "loading": "confirmed",
    "loaded": "loaded",
    "shipped": "shipped",
    "rejected": "cancelled",
    "cancelled": "cancelled",
}

PUBLIC_STATUS_LABELS = {
    "pending": "На рассмотрении",
    "confirmed": "Ожидает загрузки",
    "loaded": "Готов к выезду",
    "shipped": "Отгружено",
    "cancelled": "Отменён",
}

# `loaded` виден в списках отдельным бизнес-этапом, но вручную его не ставят:
# в него переводит только завершение погрузки/AI-счёта.
PUBLIC_MANUAL_STATUSES = ("pending", "confirmed", "shipped", "cancelled")

# Новая заявка: её ещё разбирают — подтверждают или отклоняют.
REVIEWABLE_STATUSES = ("draft", "pending")

# Заказ ждёт отгрузки: подтверждён и ещё не выехал — на любом шаге поста.
# Мешки таких заказов уже обещаны клиентам, хотя со склада ещё не списаны.
AWAITING_SHIPMENT_STATUSES = ("confirmed", "arrived", "loading", "loaded")

# Живой заказ держит камеру погрузки: одна камера — один такой заказ
# (частичный UNIQUE ``orders_one_active_order_per_loading_camera``).
CAMERA_BINDING_STATUSES = ("confirmed", "arrived", "loading")

# Машина на посту: заехала, грузится или ждёт выезда. Погрузка идёт, пока
# заказ не выедет или его не вернут, — сколько бы дней она ни длилась. Такую
# отгрузку сначала завершают или возвращают — голой сменой статуса её не бросают.
ON_POST_STATUSES = ("arrived", "loading", "loaded")

# Машина уже заехала: на посту или выехала. Номер, вид транспорта и отдел
# попали на пост, камеры и накладную — менять их поздно.
ENTERED_POST_STATUSES = (*ON_POST_STATUSES, "shipped")

# Заказ в этих статусах ещё (или уже) не является финансовым документом:
# черновик и «на рассмотрении» не подтверждены, отказ и отмена аннулированы.
# Ни один из них не входит в оборот, выручку и долги.
NON_FINANCIAL_STATUSES = frozenset({"draft", "pending", "rejected", "cancelled"})


def is_financial(status: str) -> bool:
    """Учитывается ли заказ в денежных итогах (оборот, выручка, долг)."""
    return status not in NON_FINANCIAL_STATUSES


# Заказ закрыт: дальше по нему ничего не происходит. Отгруженный не входит —
# он завершён логистически, но может быть ещё не оплачен.
CLOSED_STATUSES = frozenset({"rejected", "cancelled"})


def is_payment_open(status: str, *, method: str | None = None, by_client: bool = False) -> bool:
    """Можно ли сейчас принять оплату по заказу в статусе ``status``.

    После отгрузки — любым способом. До отгрузки (предоплата) — только
    сотрудник и только деньги, которые уже у кассы (``Payment.SETTLED_ON_RECORD``:
    наличные, свой Kaspi-терминал, «удалённо»). Kaspi QR и счёт на телефон ждут
    отгрузки: поздняя оплата старого QR на отменённом заказе увела бы деньги из
    учёта. ``method=None`` — запрос денег (QR, счёт), а не запись полученных.
    Портал клиента платит только за отгруженное. Оплата логистику не блокирует.
    """
    from .models import Payment

    if status == "shipped":
        return True
    if by_client:
        return False
    return status in AWAITING_SHIPMENT_STATUSES and method in Payment.SETTLED_ON_RECORD


def is_payment_method_allowed(currency: str, method: str | None) -> bool:
    """Допускает ли валюта заказа деньги этим способом.

    Kaspi (свой терминал, QR, счёт на телефон) и удалённая оплата — только в
    тенге: долларовый заказ касса принимает только наличными. ``method=None`` —
    запрос денег (QR, счёт), как в :func:`is_payment_open`.
    """
    return currency == "KZT" or method == "cash"


def payment_open_method(method: str, stage: str) -> str | None:
    """Способ для :func:`is_payment_open` у оплаты на шаге ``stage``.

    Запрошенная оплата (счёт, Kaspi QR через ApiPay) — ещё не деньги у кассы,
    а запрос денег: до отгрузки она закрыта при любом способе.
    """
    return method if stage == "received" else None


def is_in_progress(status: str) -> bool:
    """Заказ «сейчас в работе»: не отгружен и не закрыт.

    Это НЕ то же самое, что :func:`is_financial`: черновик и «на рассмотрении»
    в работе находятся, а в оборот ещё не входят.
    """
    return status != "shipped" and status not in CLOSED_STATUSES


def public_status_key(status: str) -> str:
    return PUBLIC_STATUS_GROUPS.get(status, status)


def statuses_in_group(group: str) -> list[str]:
    """Внутренние статусы, входящие в публичную группу (для фильтров списка)."""
    return [s for s, g in PUBLIC_STATUS_GROUPS.items() if g == group]


def public_status_label(status: str) -> str:
    key = public_status_key(status)
    return PUBLIC_STATUS_LABELS.get(key, status)
