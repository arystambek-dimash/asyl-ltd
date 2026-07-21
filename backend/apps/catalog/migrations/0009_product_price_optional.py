from django.db import migrations, models


def clear_legacy_prices(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.update(price=None)


def restore_zero_prices(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Product.objects.filter(price__isnull=True).update(price=0)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0008_product_ask_truck_weight")]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="price",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.RunPython(clear_legacy_prices, restore_zero_prices),
    ]
