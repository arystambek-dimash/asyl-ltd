from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0010_remove_client_department_and_manager"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="company_name",
            field=models.CharField(
                blank=True, default="", max_length=200,
                verbose_name="Название ТОО / ИП",
            ),
        ),
    ]
