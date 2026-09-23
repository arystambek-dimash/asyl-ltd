"""Области грузчика: «Фуры» и «Вагоны» со своими правами.

Раньше страница грузчика открывала любой транспорт, поэтому каждый обладатель
loader.view или loader.confirm получает обе области — ни у кого доступ не пропадает.
"""

from typing import ClassVar

from django.db import migrations

AREAS = {"trucks": "Фуры", "wagons": "Вагоны"}


def add_loader_areas(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Employee = apps.get_model("employees", "Employee")
    database = schema_editor.connection.alias
    areas = [
        Permission.objects.using(database).update_or_create(
            code=f"loader.{action}",
            defaults={"section": "loader", "action": action, "label": f"Грузчик: {label}"},
        )[0]
        for action, label in AREAS.items()
    ]
    holders = Employee.objects.using(database).filter(
        permissions__code__in=("loader.view", "loader.confirm"),
    ).distinct()
    for employee in holders.iterator():
        employee.permissions.add(*areas)


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0026_stores_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(add_loader_areas, migrations.RunPython.noop),
    ]
