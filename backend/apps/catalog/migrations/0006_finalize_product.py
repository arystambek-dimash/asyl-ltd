from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0005_migrate_product_data"),
    ]

    operations = [
        # new_weight_kg → weight_kg (the property weight_kg is gone in the final model)
        migrations.RemoveField(model_name="product", name="grade"),
        migrations.RemoveField(model_name="product", name="packaging"),
        migrations.RemoveField(model_name="product", name="cv_class_old"),
        migrations.RenameField(
            model_name="product", old_name="new_weight_kg", new_name="weight_kg"
        ),
        migrations.AlterField(
            model_name="product",
            name="name",
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name="product",
            name="color",
            field=models.CharField(
                max_length=10,
                choices=[("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")],
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="weight_kg",
            field=models.DecimalField(
                max_digits=6, decimal_places=2,
                choices=[(Decimal("25"), "25 кг"), (Decimal("50"), "50 кг")],
            ),
        ),
        migrations.AlterUniqueTogether(
            name="product",
            unique_together={("name", "color", "weight_kg")},
        ),
        migrations.DeleteModel(name="Grade"),
        migrations.DeleteModel(name="Packaging"),
    ]
