from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0009_always_on_daily_analytics"),
    ]

    operations = [
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="last_per_color",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="alwaysondailyanalytics",
            name="model_per_color",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
