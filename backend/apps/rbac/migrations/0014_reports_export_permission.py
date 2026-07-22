from django.db import migrations


CODE = "reports.export"


def add_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    permission, _ = Permission.objects.update_or_create(
        code=CODE,
        defaults={
            "section": "reports",
            "action": "export",
            "label": "Отчёты: Получение выписки",
        },
    )
    # Сохраняем текущий доступ после релиза. Далее администратор может
    # независимо от просмотра аналитики убрать или выдать выгрузку выписок.
    for role in Role.objects.filter(permissions__code="reports.view").distinct():
        role.permissions.add(permission)


def remove_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0013_relabel_train_wagon")]
    operations = [migrations.RunPython(add_permission, remove_permission)]
