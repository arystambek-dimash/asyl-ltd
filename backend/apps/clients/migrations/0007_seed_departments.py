from django.db import migrations


def seed(apps, schema_editor):
    Department = apps.get_model("clients", "Department")
    for code, name in (("main", "Отдел 1"), ("field", "Сити")):
        Department.objects.get_or_create(code=code, defaults={"name": name})


def unseed(apps, schema_editor):
    apps.get_model("clients", "Department").objects.filter(
        code__in=["main", "field"]).delete()


class Migration(migrations.Migration):
    dependencies = [("clients", "0006_department")]
    operations = [migrations.RunPython(seed, unseed)]
