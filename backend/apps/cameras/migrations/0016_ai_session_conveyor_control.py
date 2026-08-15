from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cameras", "0015_wagon_number_camera_source")]

    operations = [
        migrations.AddField(
            model_name="aicountingsession",
            name="target_total",
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="aicountingsession",
            name="conveyor_enabled",
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
