from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0008_always_on_camera_sources"),
    ]

    operations = [
        migrations.CreateModel(
            name="AlwaysOnCounterCursor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("camera", models.CharField(max_length=32, unique=True)),
                ("last_total", models.PositiveIntegerField(default=0)),
                ("last_mode", models.CharField(blank=True, default="", max_length=16)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="AlwaysOnDailyAnalytics",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("camera", models.CharField(max_length=32)),
                ("day", models.DateField(db_index=True)),
                ("model_total", models.PositiveIntegerField(default=0)),
                ("adjustment", models.IntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["camera"]},
        ),
        migrations.AddConstraint(
            model_name="alwaysondailyanalytics",
            constraint=models.UniqueConstraint(
                fields=("camera", "day"),
                name="cameras_one_always_on_total_per_day",
            ),
        ),
    ]
