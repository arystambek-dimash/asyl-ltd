from decimal import Decimal
from django.db import migrations

GRADE_COLOR = {"Красный": "Red", "Зелёный": "Green", "Синий": "Blue",
               "Красная": "Red", "Зелёная": "Green", "Синяя": "Blue"}


def forward(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    for p in Product.objects.select_related("grade", "packaging").all():
        p.name = p.grade.name if p.grade_id else (p.name or "Товар")
        if p.packaging_id:
            p.new_weight_kg = p.packaging.weight_kg
        elif p.new_weight_kg is None:
            p.new_weight_kg = Decimal("50")
        color = ""
        if p.cv_class_old:
            color = p.cv_class_old.split("_")[0]
        if color not in ("Red", "Green", "Blue"):
            color = GRADE_COLOR.get(p.grade.name if p.grade_id else "", "Red")
        p.color = color
        p.save(update_fields=["name", "new_weight_kg", "color"])


def backward(apps, schema_editor):
    pass  # one-way


class Migration(migrations.Migration):
    dependencies = [("catalog", "0004_alter_product_unique_together_product_color_and_more")]
    operations = [migrations.RunPython(forward, backward)]
