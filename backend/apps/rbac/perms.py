# Единый источник истины для кодов прав. Все ссылки импортируют отсюда.

_SECTIONS = {
    "catalog": ("Товары", ["view", "create", "edit", "delete"]),
    "clients": ("Клиенты", ["view", "create", "edit", "delete"]),
    "warehouse": ("Склад", ["view", "adjust"]),
    "orders": ("Заказы", ["view", "create", "edit", "confirm"]),
    "payments": ("Оплаты", ["view", "create", "confirm", "cashier"]),
    "shipping": ("Пост отгрузки", ["view", "arrive", "load", "ship", "debt_override"]),
    "train": ("Поезд", ["view", "load"]),
    "dept2": ("Отдел «Сити»", ["view", "create", "view_all"]),
    "events": ("Журнал", ["view"]),
    "reports": ("Отчёты", ["view"]),
    "employees": ("Сотрудники", ["view", "manage"]),
    "rbac": ("Доступы", ["view", "manage"]),
}

_ACTION_LABELS = {
    "view": "Просмотр", "create": "Создание", "edit": "Редактирование",
    "delete": "Удаление", "adjust": "Корректировка", "confirm": "Подтверждение",
    "cashier": "Подтверждение кассой", "view_all": "Все данные отдела",
    "arrive": "Приём машины", "load": "Загрузка", "ship": "Отгрузка",
    "debt_override": "Отгрузка в долг", "manage": "Управление",
}

# train.load переиспользует label "Загрузка" из _ACTION_LABELS.

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
    # Бухгалтер: табло — подтверждение заказов, отправка (orders.edit),
    # сверка оплат по обоим отделам (dept2.view_all).
    "Бухгалтер": _codes("payments.view", "payments.create", "payments.confirm",
                        "orders.view", "orders.confirm", "orders.edit", "dept2.view_all",
                        "clients.view", "reports.view", "events.view"),
    "Оператор": _codes("shipping.view", "shipping.arrive", "shipping.load",
                       "shipping.ship", "orders.view", "warehouse.view", "events.view"),
    "Загрузчик": _codes("train.view", "train.load"),
    # Кассир: финальное подтверждение поступления денег по обоим отделам.
    "Кассир": _codes("payments.view", "payments.cashier", "orders.view", "dept2.view_all"),
    # Менеджер выездного отдела: работает только в разделе «Сити» со своими данными.
    "Менеджер Сити": _codes("dept2.view", "dept2.create",
                            "payments.view", "payments.create"),
    "Начальник": _codes("catalog", "clients", "orders", "payments", "warehouse",
                        "shipping", "train", "dept2", "employees", "rbac",
                        "reports.view", "events.view"),
}
