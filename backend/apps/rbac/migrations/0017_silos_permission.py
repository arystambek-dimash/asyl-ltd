from django.db import migrations


def seed_silos(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    from apps.rbac.perms import PERMISSIONS

    # Пересев каталога из единого источника (идемпотентно): добавляется
    # секция «Силосы» (silos.view) — вкладка отделена от зернового процесса.
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)

    # Раньше вкладку «Силосы» видели все с grain.view. Чтобы разделение не
    # отняло доступ молча, такие роли получают silos.view автоматически;
    # дальше админ волен забрать его точечно.
    silos_view = Permission.objects.get(code="silos.view")
    for role in Role.objects.filter(permissions__code="grain.view").distinct():
        role.permissions.add(silos_view)


def unseed_silos(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code__startswith="silos.").delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0016_order_price_correction_permission")]
    operations = [migrations.RunPython(seed_silos, unseed_silos)]
