from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0005_monoblock_camera_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="monoblockcamerasettings",
            name="camera_names",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
