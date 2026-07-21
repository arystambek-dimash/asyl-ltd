from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cameras", "0007_shipping_history_settings")]

    operations = [
        migrations.AddField(
            model_name="monoblockcamerasettings",
            name="always_on_camera_sources",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
