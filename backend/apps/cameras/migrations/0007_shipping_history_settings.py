from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cameras", "0006_monoblock_camera_settings_camera_names")]

    operations = [
        migrations.AddField(
            model_name="aicountingsession",
            name="recording_stream",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="monoblockcamerasettings",
            name="completed_orders_days",
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
