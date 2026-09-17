from typing import ClassVar

from django.db import migrations

PERMISSION = {
    "code": "orders.confirm_all",
    "section": "orders",
    "action": "confirm_all",
    "label": "Заказы: Заявки всех отделов",
}


def ensure_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.using(schema_editor.connection.alias).update_or_create(
        code=PERMISSION["code"],
        defaults=PERMISSION,
    )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("rbac", "0023_loader_permissions"),
    ]

    operations: ClassVar[list] = [
        migrations.RunPython(ensure_permission, migrations.RunPython.noop),
    ]
