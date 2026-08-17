from typing import ClassVar

from django.db import migrations

PERMISSION = {
    "code": "ai_247.manage",
    "section": "ai_247",
    "action": "manage",
    "label": "AI 24/7: Управление",
}


def ensure_ai_247_manage_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    database = schema_editor.connection.alias
    Permission.objects.using(database).update_or_create(
        code=PERMISSION["code"],
        defaults=PERMISSION,
    )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0020_client_manage_access_permission"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(
            ensure_ai_247_manage_permission,
            migrations.RunPython.noop,
        ),
    ]
