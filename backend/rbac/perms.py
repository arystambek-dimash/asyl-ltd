# Единый источник истины для кодов прав. Все ссылки импортируют отсюда.

_SECTIONS = {
    "catalog": ("Номенклатура", ["view", "create", "edit", "delete"]),
    "clients": ("Клиенты", ["view", "create", "edit", "delete"]),
    "warehouse": ("Склад", ["view", "adjust"]),
    "orders": ("Заказы", ["view", "create", "edit", "confirm"]),
    "payments": ("Оплаты", ["view", "create", "confirm"]),
    "shipping": ("Пост отгрузки", ["view", "arrive", "load", "ship", "debt_override"]),
    "events": ("Журнал", ["view"]),
    "reports": ("Отчёты", ["view"]),
    "employees": ("Сотрудники", ["view", "manage"]),
    "cameras": ("Камеры", ["view", "manage"]),
}

_ACTION_LABELS = {
    "view": "Просмотр", "create": "Создание", "edit": "Редактирование",
    "delete": "Удаление", "adjust": "Корректировка", "confirm": "Подтверждение",
    "arrive": "Приём машины", "load": "Загрузка", "ship": "Отгрузка",
    "debt_override": "Отгрузка в долг", "manage": "Управление",
}

PERMISSIONS = [
    {"code": f"{sec}.{act}", "section": sec, "action": act,
     "label": f"{sec_label}: {_ACTION_LABELS[act]}"}
    for sec, (sec_label, acts) in _SECTIONS.items()
    for act in acts
]
ALL_CODES = {p["code"] for p in PERMISSIONS}
SECTION_LABELS = {sec: lbl for sec, (lbl, _) in _SECTIONS.items()}


def _codes(*sections_or_codes):
    out = []
    for x in sections_or_codes:
        if x in _SECTIONS:
            out += [f"{x}.{a}" for a in _SECTIONS[x][1]]
        else:
            out.append(x)
    return out


PRESETS = {
    "Менеджер": _codes("catalog", "clients", "orders",
                       "payments.view", "payments.confirm", "reports.view", "events.view"),
    "Бухгалтер": _codes("payments.view", "payments.create", "payments.confirm",
                        "orders.view", "clients.view", "reports.view", "events.view"),
    "Оператор": _codes("shipping.view", "shipping.arrive", "shipping.load",
                       "shipping.ship", "orders.view", "warehouse.view", "events.view"),
    "Начальник": _codes("catalog", "clients", "orders", "payments.view",
                        "payments.create", "payments.confirm", "warehouse", "shipping",
                        "cameras", "reports.view", "events.view"),
}
