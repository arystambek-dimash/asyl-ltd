"""Frozen role/catalog data used only by historical migrations.

Runtime authorization no longer has roles.  Keeping this snapshot separate
allows a fresh database to replay migrations 0001-0017 without reintroducing
role presets into the application API.
"""

_SECTIONS = {
    "catalog": ("Товары", ["view", "create", "edit", "delete"]),
    "clients": ("Клиенты", ["view", "create", "edit", "delete", "set_price"]),
    "warehouse": ("Склад", ["view", "adjust"]),
    "silos": ("Силосы", ["view"]),
    "orders": ("Заказы", ["view", "create", "edit", "confirm", "correct_price"]),
    "payments": ("Оплаты", ["view", "create", "confirm"]),
    "shipping": (
        "Пост отгрузки",
        ["view", "arrive", "load", "ship", "rollback", "debt_override"],
    ),
    "train": ("Вагон", ["view", "load"]),
    "events": ("Журнал", ["view"]),
    "reports": ("Отчёты", ["view", "export"]),
    "employees": ("Сотрудники", ["view", "manage"]),
    "rbac": ("Доступы", ["view", "manage"]),
    "tasks": ("Задачи", ["view", "create"]),
    "grain": (
        "Приход зерна",
        [
            "view", "supply", "arrive", "weigh", "lab", "dispatch",
            "unload", "inventory", "exit", "delete", "admin",
        ],
    ),
}

_ACTION_LABELS = {
    "view": "Просмотр",
    "create": "Создание",
    "edit": "Редактирование",
    "delete": "Удаление",
    "adjust": "Корректировка",
    "confirm": "Подтверждение",
    "arrive": "Приём машины",
    "load": "Загрузка",
    "ship": "Отгрузка",
    "debt_override": "Отгрузка в долг",
    "manage": "Управление",
    "rollback": "Откат отгрузки",
    "set_price": "Закрепление прайса",
    "correct_price": "Корректировка стоимости",
    "export": "Получение выписки",
    "supply": "Заявки на поставку",
    "weigh": "Взвешивание",
    "lab": "Лаборатория",
    "dispatch": "Диспетчер",
    "unload": "Разгрузка",
    "inventory": "Оприходование",
    "exit": "Выезд",
    "admin": "Администрирование",
}

PERMISSIONS = [
    {
        "code": f"{section}.{action}",
        "section": section,
        "action": action,
        "label": f"{section_label}: {_ACTION_LABELS[action]}",
    }
    for section, (section_label, actions) in _SECTIONS.items()
    for action in actions
]


def _section_codes(*sections_or_codes):
    codes = []
    for value in sections_or_codes:
        if value in _SECTIONS:
            codes.extend(f"{value}.{action}" for action in _SECTIONS[value][1])
        else:
            codes.append(value)
    return codes


PRESETS = {
    "Менеджер": _section_codes(
        "catalog", "clients", "orders", "payments.view",
        "payments.confirm", "reports", "events.view",
    ),
    "Касса": _section_codes(
        "payments.view", "payments.create", "payments.confirm",
        "orders.view", "orders.confirm", "orders.edit", "clients.view",
        "reports", "events.view",
    ),
    "Оператор": _section_codes(
        "shipping.view", "shipping.arrive", "shipping.load",
        "shipping.ship", "orders.view", "warehouse.view", "events.view",
    ),
    "Загрузчик": _section_codes("train.view", "train.load"),
    "Контролёр": _section_codes(
        "shipping.view", "shipping.arrive", "shipping.load", "shipping.ship",
        "shipping.debt_override", "train", "orders.view", "warehouse.view",
    ),
    "Начальник": _section_codes(
        "catalog", "clients", "orders", "payments", "warehouse", "silos",
        "shipping", "train", "employees", "rbac", "reports",
        "events.view", "tasks",
    ),
}
