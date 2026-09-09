from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0036_clear_legacy_portal_department")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="rejection_reason",
            field=models.CharField(
                blank=True, default="", db_default="", max_length=500
            ),
        ),
    ]
