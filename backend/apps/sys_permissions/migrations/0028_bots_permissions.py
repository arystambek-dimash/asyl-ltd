"""Журнал WhatsApp-бота: права «WhatsApp-бот» — журнал и проведение сообщений.

Страница новая, поэтому доступ никто не теряет. Сразу видят и разбирают
журнал администраторы (sys_permissions.manage) — им же принадлежат настройки
бота; остальным права выдают в «Сотрудниках».
"""

from typing import ClassVar

from django.db import migrations

ACTIONS = {"view": "Журнал сообщений", "manage": "Провести и пропустить сообщение"}


def add_bot_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Employee = apps.get_model("employees", "Employee")
    database = schema_editor.connection.alias
    permissions = [
        Permission.objects.using(database).update_or_create(
            code=f"bots.{action}",
            defaults={"section": "bots", "action": action, "label": f"WhatsApp-бот: {label}"},
        )[0]
        for action, label in ACTIONS.items()
    ]
    admins = Employee.objects.using(database).filter(permissions__code="sys_permissions.manage").distinct()
    for employee in admins.iterator():
        employee.permissions.add(*permissions)


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0027_loader_transport_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(add_bot_permissions, migrations.RunPython.noop),
    ]
