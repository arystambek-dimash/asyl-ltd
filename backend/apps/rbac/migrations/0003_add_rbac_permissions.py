from django.db import migrations


RBAC_PERMISSIONS = [
    {
        "code": "rbac.view",
        "section": "rbac",
        "action": "view",
        "label": "Доступы: Просмотр",
    },
    {
        "code": "rbac.manage",
        "section": "rbac",
        "action": "manage",
        "label": "Доступы: Управление",
    },
]

BOSS_EXTRA_CODES = ["employees.view", "employees.manage", "rbac.view", "rbac.manage"]


def seed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")

    for permission in RBAC_PERMISSIONS:
        Permission.objects.update_or_create(
            code=permission["code"],
            defaults=permission,
        )

    boss = Role.objects.filter(name="Начальник", is_system=True).first()
    if boss:
        boss.permissions.add(*Permission.objects.filter(code__in=BOSS_EXTRA_CODES))


def unseed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")

    Permission.objects.filter(code__in=[p["code"] for p in RBAC_PERMISSIONS]).delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0002_seed")]
    operations = [migrations.RunPython(seed, unseed)]
