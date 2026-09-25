"""Упрощение каталога прав: разделы = страницы меню, Моноблок одним правом.

Сотрудники ничего не теряют: обладатели снятых кодов получают их замену
(объединение), затем снятые коды удаляются, подписи остальных обновляются.
Дизайн и реализация: коммит 18c9343.
"""

from typing import ClassVar

from django.db import migrations

# Каталог на момент миграции (копия perms.py): рабочий каталог дальше может меняться.
CATALOG = [
    {"code": "reports.view", "section": "reports", "action": "view", "label": "Отчёты: Просмотр"},
    {"code": "reports.export", "section": "reports", "action": "export", "label": "Отчёты: Выписки Excel"},
    {"code": "orders.view", "section": "orders", "action": "view", "label": "Заказы: Просмотр и запрос смены статуса"},
    {"code": "orders.create", "section": "orders", "action": "create", "label": "Заказы: Создание"},
    {"code": "orders.edit", "section": "orders", "action": "edit", "label": "Заказы: Изменение и удаление"},
    {"code": "orders.confirm", "section": "orders", "action": "confirm", "label": "Заказы: Подтверждение заявок"},
    {"code": "orders.confirm_all", "section": "orders", "action": "confirm_all", "label": "Заказы: Заявки всех отделов"},
    {"code": "orders.correct_price", "section": "orders", "action": "correct_price", "label": "Заказы: Корректировка стоимости"},
    {"code": "orders.rollback", "section": "orders", "action": "rollback", "label": "Заказы: Откат отгрузки"},
    {"code": "payments.view", "section": "payments", "action": "view", "label": "Касса: Транзакции"},
    {"code": "payments.create", "section": "payments", "action": "create", "label": "Касса: Приём оплат и POS"},
    {"code": "payments.confirm", "section": "payments", "action": "confirm", "label": "Касса: Подтверждение оплат и возвраты"},
    {"code": "monoblock.view", "section": "monoblock", "action": "view", "label": "Моноблок: Доступ (видит всё)"},
    {"code": "loader.view", "section": "loader", "action": "view", "label": "Грузчик: Очередь и накладные"},
    {"code": "loader.confirm", "section": "loader", "action": "confirm", "label": "Грузчик: Отгрузить (списание со склада)"},
    {"code": "warehouse.view", "section": "warehouse", "action": "view", "label": "Склады: Просмотр"},
    {"code": "warehouse.adjust", "section": "warehouse", "action": "adjust", "label": "Склады: Приход, перемещение и корректировка"},
    {"code": "silos.view", "section": "silos", "action": "view", "label": "Силосы: Просмотр"},
    {"code": "grain.view", "section": "grain", "action": "view", "label": "Приход и вывоз: Просмотр"},
    {"code": "grain.supply", "section": "grain", "action": "supply", "label": "Приход и вывоз: Новый приход"},
    {"code": "grain.arrive", "section": "grain", "action": "arrive", "label": "Приход и вывоз: Приём поезда и оформление вывоза"},
    {"code": "grain.weigh", "section": "grain", "action": "weigh", "label": "Приход и вывоз: Взвешивание и остановки под аркой"},
    {"code": "grain.correct_weighing", "section": "grain", "action": "correct_weighing", "label": "Приход и вывоз: Ручной заезд и правка выездного веса"},
    {"code": "grain.inventory", "section": "grain", "action": "inventory", "label": "Приход и вывоз: Расхождения веса и корректировка силосов"},
    {"code": "grain.delete", "section": "grain", "action": "delete", "label": "Приход и вывоз: Удаление рейса"},
    {"code": "grain.admin", "section": "grain", "action": "admin", "label": "Приход и вывоз: Настройка силосов и видов зерна"},
    {"code": "clients.view", "section": "clients", "action": "view", "label": "Клиенты: Просмотр"},
    {"code": "clients.create", "section": "clients", "action": "create", "label": "Клиенты: Создание"},
    {"code": "clients.edit", "section": "clients", "action": "edit", "label": "Клиенты: Изменение"},
    {"code": "clients.delete", "section": "clients", "action": "delete", "label": "Клиенты: Удаление"},
    {"code": "clients.set_price", "section": "clients", "action": "set_price", "label": "Клиенты: Цены клиента"},
    {"code": "clients.manage_access", "section": "clients", "action": "manage_access", "label": "Клиенты: Доступ в кабинет клиента"},
    {"code": "catalog.view", "section": "catalog", "action": "view", "label": "Товары: Просмотр"},
    {"code": "catalog.create", "section": "catalog", "action": "create", "label": "Товары: Создание"},
    {"code": "catalog.edit", "section": "catalog", "action": "edit", "label": "Товары: Изменение и архив"},
    {"code": "tasks.view", "section": "tasks", "action": "view", "label": "Задачи: Задачи всех сотрудников"},
    {"code": "tasks.create", "section": "tasks", "action": "create", "label": "Задачи: Создание"},
    {"code": "events.view", "section": "events", "action": "view", "label": "Журнал: Журнал событий"},
    {"code": "employees.view", "section": "employees", "action": "view", "label": "Сотрудники: Просмотр"},
    {"code": "employees.manage", "section": "employees", "action": "manage", "label": "Сотрудники: Изменение профилей"},
    {"code": "sys_permissions.manage", "section": "sys_permissions", "action": "manage", "label": "Администрирование: Права, отделы, настройки камер и накладной"},
]

# Снятый код -> что получает каждый его обладатель.
GRANTS = {
    "shipping.view": ("monoblock.view",),
    # Тот, кто грузил и отгружал в Моноблоке, отгружает теперь на странице «Грузчик».
    "shipping.load": ("monoblock.view", "loader.view", "loader.confirm"),
    "shipping.ship": ("monoblock.view", "loader.view", "loader.confirm"),
    "train.view": ("monoblock.view",),
    "train.load": ("monoblock.view", "loader.view", "loader.confirm"),
    "ai_247.manage": ("monoblock.view",),
    "shipping.rollback": ("orders.rollback",),
}

RETIRED = (
    *GRANTS,
    # Ни на что не влияли.
    "shipping.arrive",
    "shipping.debt_override",
    "sys_permissions.view",
    "catalog.delete",
    # Старый процесс вагонов: его действия теперь у grain.admin.
    "grain.lab",
    "grain.dispatch",
    "grain.unload",
    "grain.exit",
)


def simplify_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Employee = apps.get_model("employees", "Employee")
    database = schema_editor.connection.alias
    by_code = {
        row["code"]: Permission.objects.using(database).update_or_create(code=row["code"], defaults=row)[0]
        for row in CATALOG
    }
    for old_code, new_codes in GRANTS.items():
        old = Permission.objects.using(database).filter(code=old_code).first()
        if old is None:
            continue
        targets = [by_code[code] for code in new_codes]
        for employee in Employee.objects.using(database).filter(permissions=old).distinct().iterator():
            employee.permissions.add(*targets)
    Permission.objects.using(database).filter(code__in=RETIRED).delete()


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0024_orders_confirm_all_permission"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(simplify_permissions, migrations.RunPython.noop),
    ]
