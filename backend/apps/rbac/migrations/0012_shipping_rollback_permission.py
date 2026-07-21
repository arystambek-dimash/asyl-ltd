from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    permission, _ = Permission.objects.update_or_create(
        code="shipping.rollback",
        defaults={
            "section": "shipping",
            "action": "rollback",
            "label": "Пост отгрузки: Откат отгрузки",
        },
    )
    # Опасное действие не выдаём менеджерам и операторам автоматически.
    # Оно доступно суперпользователю и системной роли руководителя.
    boss = Role.objects.filter(name="Начальник").first()
    if boss:
        boss.permissions.add(permission)


def unseed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code="shipping.rollback").delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0011_remove_static_city_department")]
    operations = [migrations.RunPython(seed_permission, unseed_permission)]
