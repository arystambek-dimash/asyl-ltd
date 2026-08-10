from django.db import migrations

# The historical Django app label intentionally remains "rbac".


CODE = "orders.correct_price"


def add_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    permission, _ = Permission.objects.update_or_create(
        code=CODE,
        defaults={
            "section": "orders",
            "action": "correct_price",
            "label": "Заказы: Корректировка стоимости",
        },
    )
    # Сразу оставляем аварийную корректировку руководителям, которые уже
    # управляют ролями. Остальным доступ выдаётся явно в карточке роли/сотрудника.
    for role in Role.objects.filter(permissions__code="rbac.manage").distinct():
        role.permissions.add(permission)


def remove_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0015_grain_permissions")]
    operations = [migrations.RunPython(add_permission, remove_permission)]
