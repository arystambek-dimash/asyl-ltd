from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0009_dynamic_departments"),
    ]

    operations = [
        migrations.RemoveField(model_name="client", name="department"),
        migrations.RemoveField(model_name="client", name="manager"),
    ]
