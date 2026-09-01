from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0025_always_on_brand_analytics"),
    ]

    operations = [
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="continuous_analytics",
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
