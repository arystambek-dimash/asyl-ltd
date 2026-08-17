_SECTIONS = {
    "catalog": ("Товары", ["view", "create", "edit", "delete"]),
    "clients": (
        "Клиенты",
        ["view", "create", "edit", "delete", "set_price", "manage_access"],
    ),
    "warehouse": ("Склад", ["view", "adjust"]),
    "silos": ("Силосы", ["view"]),
    "orders": ("Заказы", ["view", "create", "edit", "confirm", "correct_price"]),
    "payments": ("Оплаты", ["view", "create", "confirm"]),
    "shipping": ("Пост отгрузки", ["view", "arrive", "load", "ship", "rollback", "debt_override"]),
    "ai_247": ("AI 24/7", ["manage"]),
    "train": ("Вагон", ["view", "load"]),
    "events": ("Журнал", ["view"]),
    "reports": ("Отчёты", ["view", "export"]),
    "employees": ("Сотрудники", ["view", "manage"]),
    "sys_permissions": ("Системные права", ["view", "manage"]),
    "tasks": ("Задачи", ["view", "create"]),
    "grain": ("Приход зерна", [
        "view", "supply", "arrive", "weigh", "lab", "dispatch", "unload",
        "inventory", "exit", "delete", "admin",
    ]),
}

_ACTION_LABELS = {
    "view": "Просмотр", "create": "Создание", "edit": "Редактирование",
    "delete": "Удаление", "adjust": "Корректировка", "confirm": "Подтверждение",
    "arrive": "Приём машины", "load": "Загрузка", "ship": "Отгрузка",
    "debt_override": "Отгрузка в долг", "manage": "Управление",
    "rollback": "Откат отгрузки",
    "set_price": "Закрепление прайса",
    "manage_access": "Доступ к порталу",
    "correct_price": "Корректировка стоимости",
    "export": "Получение выписки",
    "supply": "Заявки на поставку", "weigh": "Взвешивание",
    "lab": "Лаборатория", "dispatch": "Диспетчер",
    "unload": "Разгрузка", "inventory": "Оприходование",
    "exit": "Выезд", "admin": "Администрирование",
}

PERMISSIONS = [
    {"code": f"{sec}.{act}", "section": sec, "action": act,
     "label": f"{sec_label}: {_ACTION_LABELS[act]}"}
    for sec, (sec_label, acts) in _SECTIONS.items()
    for act in acts
]
ALL_CODES = {p["code"] for p in PERMISSIONS}
