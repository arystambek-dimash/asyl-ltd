from django.db import migrations


def seed_controller(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    from apps.rbac.perms import PERMISSIONS, PRESETS
    # Идемпотентный ресид из единого источника: добавляется роль «Контролёр».
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
    dependencies = [("rbac", "0007_dept2_payment_chain")]
    operations = [migrations.RunPython(seed_controller, noop)]
