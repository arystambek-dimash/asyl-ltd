from django.db import migrations, models
from django.utils import timezone


def prepare_departments(apps, schema_editor):
    Department = apps.get_model("clients", "Department")
    palette = {
        "main": "#315FD5",
        "field": "#D68B2C",
    }
    has_main = Department.objects.filter(code="main").exists()
    for index, department in enumerate(Department.objects.order_by("id")):
        department.color = palette.get(department.code, "#315FD5")
        department.is_active = True
        department.is_default = department.code == "main" if has_main else index == 0
        department.created_at = department.created_at or timezone.now()
        department.save(update_fields=["color", "is_active", "is_default", "created_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0008_client_created_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="department",
            name="code",
            field=models.CharField(max_length=50, unique=True),
        ),
        migrations.AddField(
            model_name="department",
            name="color",
            field=models.CharField(default="#315FD5", max_length=7),
        ),
        migrations.AddField(
            model_name="department",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name="department",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="department",
            name="is_default",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(prepare_departments, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="department",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterModelOptions(
            name="department",
            options={"ordering": ["created_at", "id"]},
        ),
    ]
