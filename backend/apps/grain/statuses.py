"""Статусы вагона и строгая машина переходов.

Один источник правды: сервисы меняют статус только через ``ensure_transition``,
поэтому «из ARRIVED сразу в COMPLETED» невозможен ни из какого API.
"""

# Основной поток
EXPECTED = "expected"
ARRIVED = "arrived"
AT_SILO = "at_silo"
GROSS_WEIGHED = "gross_weighed"
LAB_PENDING = "lab_pending"
UNLOADING_ALLOWED = "unloading_allowed"
SILO_ASSIGNED = "silo_assigned"
UNLOADING = "unloading"
UNLOADING_COMPLETED = "unloading_completed"
TARE_WEIGHED = "tare_weighed"
INVENTORIED = "inventoried"
EXIT_ALLOWED = "exit_allowed"
EXITED = "exited"
COMPLETED = "completed"

# Дополнительные
UNPLANNED = "unplanned"
WAITING_FOR_APPROVAL = "waiting_for_approval"
REJECTED = "rejected"
QUARANTINE = "quarantine"
INSUFFICIENT_CAPACITY = "insufficient_capacity"
WEIGHT_DISCREPANCY = "weight_discrepancy"
REWEIGHING_REQUIRED = "reweighing_required"
BLOCKED = "blocked"
RETURN_TO_SUPPLIER = "return_to_supplier"
CANCELLED = "cancelled"

WAGON_STATUS_LABELS = {
    EXPECTED: "Ожидается",
    ARRIVED: "Прибыл",
    AT_SILO: "У назначенного силоса",
    GROSS_WEIGHED: "Брутто взвешен",
    LAB_PENDING: "Ждёт лабораторию",
    UNLOADING_ALLOWED: "Разгрузка разрешена",
    SILO_ASSIGNED: "Силос назначен",
    UNLOADING: "Разгружается",
    UNLOADING_COMPLETED: "Разгрузка завершена",
    TARE_WEIGHED: "Тара взвешена",
    INVENTORIED: "Оприходован",
    EXIT_ALLOWED: "Выезд разрешён",
    EXITED: "Выехал",
    COMPLETED: "Завершён",
    UNPLANNED: "Незапланированный",
    WAITING_FOR_APPROVAL: "Ждёт подтверждения",
    REJECTED: "Отклонён лабораторией",
    QUARANTINE: "Карантин",
    INSUFFICIENT_CAPACITY: "Нет места в силосе",
    WEIGHT_DISCREPANCY: "Расхождение веса",
    REWEIGHING_REQUIRED: "Нужно перевесить",
    BLOCKED: "Заблокирован",
    RETURN_TO_SUPPLIER: "Возврат поставщику",
    CANCELLED: "Отменён",
}
WAGON_STATUSES = list(WAGON_STATUS_LABELS)

# Терминальные статусы: вагон больше не «на территории».
TERMINAL_STATUSES = {COMPLETED, CANCELLED, RETURN_TO_SUPPLIER}
# Рейс завершён для списков и силосов: терминальный или уже выехал.
FINISHED_STATUSES = TERMINAL_STATUSES | {EXITED}
# Статусы «на территории»: от прибытия до выезда.
ON_SITE_STATUSES = {
    ARRIVED,
    AT_SILO,
    GROSS_WEIGHED,
    LAB_PENDING,
    UNLOADING_ALLOWED,
    SILO_ASSIGNED,
    UNLOADING,
    UNLOADING_COMPLETED,
    TARE_WEIGHED,
    INVENTORIED,
    EXIT_ALLOWED,
    WAITING_FOR_APPROVAL,
    REJECTED,
    QUARANTINE,
    INSUFFICIENT_CAPACITY,
    WEIGHT_DISCREPANCY,
    REWEIGHING_REQUIRED,
    BLOCKED,
}

# Старые статусы (лаборатория, силос, разгрузка) остались только у исторических
# записей: переходов из них нет, такие рейсы можно лишь удалить.
VALID_TRANSITIONS: dict[str, set[str]] = {
    EXPECTED: {ARRIVED, CANCELLED},
    ARRIVED: {AT_SILO, BLOCKED, CANCELLED, RETURN_TO_SUPPLIER},
    AT_SILO: {TARE_WEIGHED, BLOCKED, CANCELLED, RETURN_TO_SUPPLIER},
    TARE_WEIGHED: {INVENTORIED, WEIGHT_DISCREPANCY},
    # Из расхождения нет пути в оприходование напрямую: сначала решение
    # (подтвердить вес или перевесить), потом обычный поток.
    WEIGHT_DISCREPANCY: {AT_SILO, TARE_WEIGHED},
    INVENTORIED: {EXIT_ALLOWED},
    EXIT_ALLOWED: {EXITED, BLOCKED},
    EXITED: {COMPLETED},
    BLOCKED: {ARRIVED, AT_SILO, EXIT_ALLOWED, CANCELLED},
}


# Stored values remain compatible with historical weighings and events. The
# outbound process does not permit laboratory, silo assignment or unloading.
PASSAGE_TRANSITIONS: dict[str, set[str]] = {
    ARRIVED: {AT_SILO, BLOCKED, CANCELLED},
    AT_SILO: {TARE_WEIGHED, BLOCKED, CANCELLED},
    TARE_WEIGHED: {INVENTORIED},
    INVENTORIED: {EXIT_ALLOWED},
    EXIT_ALLOWED: {EXITED, BLOCKED},
    EXITED: {COMPLETED},
    BLOCKED: {ARRIVED, AT_SILO, EXIT_ALLOWED, CANCELLED},
}


def can_transition(current: str, target: str, *, passage: bool = False) -> bool:
    transitions = PASSAGE_TRANSITIONS if passage else VALID_TRANSITIONS
    return target in transitions.get(current, set())
