from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0016_order_payment_method"),
        ("clients", "0009_dynamic_departments"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="department",
            field=models.CharField(default="main", max_length=50),
        ),
    ]
