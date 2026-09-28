"""Права на «Главную» и страницу «Задачи»: их можно скрыть от сотрудника.

Раньше обе страницы были открыты всем без права, поэтому оба права получает
каждый сотрудник — ни у кого доступ не пропадает.
"""

from typing import ClassVar

from django.db import migrations

PAGE_PERMISSIONS = {
    "dashboard.view": "Главная: Доступ",
    "tasks.own": "Задачи: Доступ (свои задачи)",
}


def add_page_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Employee = apps.get_model("employees", "Employee")
    database = schema_editor.connection.alias
    permissions = []
    for code, label in PAGE_PERMISSIONS.items():
        section, action = code.split(".")
        permissions.append(Permission.objects.using(database).update_or_create(
            code=code, defaults={"section": section, "action": action, "label": label},
        )[0])
    for employee in Employee.objects.using(database).iterator():
        employee.permissions.add(*permissions)


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0028_bots_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(add_page_permissions, migrations.RunPython.noop),
    ]
