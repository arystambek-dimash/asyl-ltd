from django.db import migrations, models
import django.db.models.deletion


def populate_product_snapshots(apps, schema_editor):
    OrderItem = apps.get_model("orders", "OrderItem")
    color_labels = {"Red": "Красный", "Green": "Зелёный", "Blue": "Синий"}
    for item in OrderItem.objects.select_related("product").iterator():
        product = item.product
        color = color_labels.get(product.color, product.color)
        item.product_label_snapshot = (
            f"{product.name} · {color} {int(product.weight_kg)} кг")
        weight = "50" if product.weight_kg == 50 else "25"
        item.product_cv_class_snapshot = f"{product.color}_{weight}"
        item.product_weight_kg_snapshot = product.weight_kg
        item.product_ask_truck_weight_snapshot = product.ask_truck_weight
        item.save(update_fields=[
            "product_label_snapshot", "product_cv_class_snapshot",
            "product_weight_kg_snapshot", "product_ask_truck_weight_snapshot",
        ])


class Migration(migrations.Migration):
    dependencies = [("orders", "0019_unique_active_loading_camera")]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="product_label_snapshot",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="product_cv_class_snapshot",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="product_weight_kg_snapshot",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="product_ask_truck_weight_snapshot",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(populate_product_snapshots, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="orderitem",
            name="product",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="order_items", to="catalog.product"),
        ),
    ]
