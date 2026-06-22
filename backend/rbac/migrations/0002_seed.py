from django.db import migrations


def seed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    from rbac.perms import PERMISSIONS, PRESETS
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)
    for name, codes in PRESETS.items():
        role, _ = Role.objects.get_or_create(name=name, defaults={"is_system": True})
        role.is_system = True
        role.save()
        role.permissions.set(Permission.objects.filter(code__in=codes))


def unseed(apps, schema_editor):
    apps.get_model("rbac", "Role").objects.filter(is_system=True).delete()
    apps.get_model("rbac", "Permission").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
