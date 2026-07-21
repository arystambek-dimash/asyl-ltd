from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0012_client_currency"),
    ]

    operations = [
        migrations.AlterField(
            model_name="client",
            name="last_name",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
