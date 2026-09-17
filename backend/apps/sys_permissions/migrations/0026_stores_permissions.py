"""Права раздела «Магазины»: страницу можно скрыть отдельно от «Клиентов».

Раньше магазины открывались правами клиентов, поэтому каждый обладатель
clients.<действие> получает stores.<действие> — ни у кого доступ не пропадает.
"""

from typing import ClassVar

from django.db import migrations

ACTIONS = {"view": "Просмотр", "create": "Создание", "edit": "Изменение", "delete": "Удаление"}


def add_store_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Employee = apps.get_model("employees", "Employee")
    database = schema_editor.connection.alias
    for action, label in ACTIONS.items():
        store_perm = Permission.objects.using(database).update_or_create(
            code=f"stores.{action}",
            defaults={"section": "stores", "action": action, "label": f"Магазины: {label}"},
        )[0]
        client_perm = Permission.objects.using(database).filter(code=f"clients.{action}").first()
        if client_perm is None:
            continue
        for employee in Employee.objects.using(database).filter(permissions=client_perm).distinct().iterator():
            employee.permissions.add(store_perm)


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0025_simplify_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(add_store_permissions, migrations.RunPython.noop),
    ]
