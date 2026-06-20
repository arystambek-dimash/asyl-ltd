from django.db import migrations


def seed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ("manager", "accountant", "operator", "boss"):
        Group.objects.get_or_create(name=name)


def unseed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(
        name__in=("manager", "accountant", "operator", "boss")
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
