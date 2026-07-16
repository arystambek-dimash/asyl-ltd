from django.db import migrations


CODE = "clients.set_price"


def add_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    permission, _ = Permission.objects.update_or_create(
        code=CODE,
        defaults={
            "section": "clients",
            "action": "set_price",
            "label": "Клиенты: Закрепление прайса",
        },
    )
    for role in Role.objects.filter(name__in=["Менеджер", "Менеджер Сити", "Начальник"]):
        role.permissions.add(permission)


def remove_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0009_remove_cashier"),
    ]

    operations = [
        migrations.RunPython(add_permission, remove_permission),
    ]
