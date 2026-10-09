"""Страница «Кладовщик»: права на приёмку возвратов товара.

Страница новая, раньше возврат проводился сразу — доступ никто не теряет.
Права кладовщику выдают в «Сотрудниках».
"""

from typing import ClassVar

from django.db import migrations

ACTIONS = {"view": "Возвраты на приёмку", "confirm": "Приёмка и закрытие возврата"}


def add_storekeeper_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    permissions = Permission.objects.using(schema_editor.connection.alias)
    for action, label in ACTIONS.items():
        permissions.update_or_create(
            code=f"storekeeper.{action}",
            defaults={"section": "storekeeper", "action": action, "label": f"Кладовщик: {label}"},
        )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0030_bots_telegram_label"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(add_storekeeper_permissions, migrations.RunPython.noop),
    ]
