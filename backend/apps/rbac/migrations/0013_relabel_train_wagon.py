from django.db import migrations


def reseed_labels(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    from apps.rbac.perms import PERMISSIONS
    # Обновляем label из единого источника (train: Поезд → Вагон).
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("rbac", "0012_shipping_rollback_permission")]
    operations = [migrations.RunPython(reseed_labels, noop)]
