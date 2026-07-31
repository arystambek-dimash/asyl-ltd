from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cameras", "0014_archive_day_rows")]

    operations = [
        migrations.AddField(
            model_name="monoblockcamerasettings",
            name="wagon_number_camera_source",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
