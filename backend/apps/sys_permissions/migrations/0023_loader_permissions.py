from typing import ClassVar

from django.db import migrations

PERMISSIONS = [
    {"code": "loader.view", "section": "loader", "action": "view", "label": "Грузчик: Просмотр"},
    {"code": "loader.confirm", "section": "loader", "action": "confirm", "label": "Грузчик: Подтверждение"},
]


def ensure_loader_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    database = schema_editor.connection.alias
    for permission in PERMISSIONS:
        Permission.objects.using(database).update_or_create(
            code=permission["code"],
            defaults=permission,
        )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0022_grain_correct_weighing_permission"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(ensure_loader_permissions, migrations.RunPython.noop),
    ]
