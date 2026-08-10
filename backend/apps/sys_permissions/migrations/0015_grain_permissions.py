from django.db import migrations


def seed_grain(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    from apps.sys_permissions.migration_data import PERMISSIONS

    # Пересев из единого источника (идемпотентно): добавляется секция
    # «Приход зерна» (grain.*). Роли не трогаем — их раскладывает админ
    # под свой штат (проходная, весовая, лаборатория, диспетчер и т.д.).
    for p in PERMISSIONS:
        Permission.objects.update_or_create(code=p["code"], defaults=p)


def unseed_grain(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code__startswith="grain.").delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0014_reports_export_permission")]
    operations = [migrations.RunPython(seed_grain, unseed_grain)]
