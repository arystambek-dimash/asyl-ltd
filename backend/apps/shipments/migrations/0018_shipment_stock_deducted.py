from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shipments", "0017_shipmentsource_db_on_delete"),
    ]

    operations = [
        # Expand: db_default в PG — быстрый default без перезаписи таблицы;
        # INSERT старого образа после автоотката получает True («склад списан»).
        migrations.AddField(
            model_name="shipment",
            name="stock_deducted",
            field=models.BooleanField(db_default=True, default=True),
        ),
    ]
