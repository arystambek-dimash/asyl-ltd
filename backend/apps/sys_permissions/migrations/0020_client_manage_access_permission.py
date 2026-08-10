from django.db import migrations

PERMISSION = {
    "code": "clients.manage_access",
    "section": "clients",
    "action": "manage_access",
    "label": "Клиенты: Доступ к порталу",
}


def ensure_client_manage_access_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    database = schema_editor.connection.alias
    Permission.objects.using(database).update_or_create(
        code=PERMISSION["code"],
        defaults=PERMISSION,
    )


class Migration(migrations.Migration):
    dependencies = [("rbac", "0019_ensure_grain_delete_permission")]

    operations = [
        migrations.RunPython(
            ensure_client_manage_access_permission,
            migrations.RunPython.noop,
        ),
    ]
