from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0023_remove_legacy_esp_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="brand",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="brand_confidence",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="classification_status",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="color",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="color_confidence",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="sku",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
