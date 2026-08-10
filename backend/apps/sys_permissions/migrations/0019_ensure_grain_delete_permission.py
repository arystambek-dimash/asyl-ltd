from django.db import migrations


PERMISSION = {
    "code": "grain.delete",
    "section": "grain",
    "action": "delete",
    "label": "Приход зерна: Удаление",
}


def ensure_grain_delete_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    database = schema_editor.connection.alias
    Permission.objects.using(database).update_or_create(
        code=PERMISSION["code"],
        defaults=PERMISSION,
    )


class Migration(migrations.Migration):
    dependencies = [("rbac", "0018_retire_roles")]

    operations = [
        migrations.RunPython(
            ensure_grain_delete_permission,
            migrations.RunPython.noop,
        ),
    ]
