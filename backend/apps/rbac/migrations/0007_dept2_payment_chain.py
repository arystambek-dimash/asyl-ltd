from django.db import migrations


def seed_dept2(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    from apps.rbac.perms import PERMISSIONS, PRESETS
    # Перезаписываем все права/пресеты из единого источника (идемпотентно):
    # добавляются dept2.*, payments.cashier и роли «Кассир», «Менеджер Сити».
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)
    for name, codes in PRESETS.items():
        role, _ = Role.objects.get_or_create(name=name, defaults={"is_system": True})
        role.is_system = True
        role.save()
        role.permissions.set(Permission.objects.filter(code__in=codes))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("rbac", "0006_relabel_catalog")]
    operations = [migrations.RunPython(seed_dept2, noop)]
