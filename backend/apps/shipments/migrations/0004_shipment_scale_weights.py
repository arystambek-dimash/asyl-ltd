from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shipments", "0003_shipment_loading_started_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="shipment",
            name="weigh_in_source",
            field=models.CharField(
                choices=[
                    ("legacy", "Старые данные"),
                    ("estimated", "Расчёт по товару"),
                    ("manual", "Ручной ввод"),
                    ("scale", "Автомобильные весы"),
                ],
                db_default="legacy",
                default="legacy",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="shipment",
            name="weigh_out_kg",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True
            ),
        ),
        migrations.AddField(
            model_name="shipment",
            name="net_weight_kg",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True
            ),
        ),
    ]
