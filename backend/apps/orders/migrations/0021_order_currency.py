from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0012_client_currency"),
        ("orders", "0020_orderitem_product_snapshot"),
    ]
    operations = [
        migrations.AddField(
            model_name="order",
            name="currency",
            field=models.CharField(
                choices=[("KZT", "KZT (тенге)"), ("USD", "USD (доллар)")],
                default="KZT",
                max_length=3,
            ),
        ),
    ]
