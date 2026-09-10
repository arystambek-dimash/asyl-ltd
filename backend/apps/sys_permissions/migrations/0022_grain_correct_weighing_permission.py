from django.db import migrations


def ensure_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.using(schema_editor.connection.alias).update_or_create(
        code="grain.correct_weighing",
        defaults={
            "section": "grain",
            "action": "correct_weighing",
            "label": "Приход зерна: Ручной заезд и исправление выездного веса",
        },
    )


class Migration(migrations.Migration):
    dependencies = [("rbac", "0021_ai_247_manage_permission")]
    operations = [migrations.RunPython(ensure_permission, migrations.RunPython.noop)]
