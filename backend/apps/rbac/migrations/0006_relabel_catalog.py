from django.db import migrations


def reseed_labels(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    from apps.rbac.perms import PERMISSIONS
    # Обновляем label/секцию из единого источника (catalog: Номенклатура → Товары).
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("rbac", "0005_train_perms")]
    operations = [migrations.RunPython(reseed_labels, noop)]
