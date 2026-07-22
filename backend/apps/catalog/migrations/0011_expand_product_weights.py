from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0010_clientprice_currency")]
    operations = [
        migrations.AlterField(
            model_name="product",
            name="weight_kg",
            field=models.DecimalField(
                choices=[
                    (Decimal("2"), "2 кг"),
                    (Decimal("5"), "5 кг"),
                    (Decimal("10"), "10 кг"),
                    (Decimal("25"), "25 кг"),
                    (Decimal("50"), "50 кг"),
                ],
                decimal_places=2,
                max_digits=6,
            ),
        ),
    ]
