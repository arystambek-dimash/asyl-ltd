from decimal import Decimal

from django.db import migrations

# 6 классов мешков = 3 цвета × 2 веса. cv_class совпадает с классами детектора
# (weights/detector.pt: Red_50/Red_25/Blue_50/Blue_25/Green_50/Green_25).
COLORS = [("Красный", "Red"), ("Синий", "Blue"), ("Зелёный", "Green")]
PACKS = [("Мешок 50 кг", Decimal("50"), "50"), ("Мешок 25 кг", Decimal("25"), "25")]
PRICE = {"50": Decimal("25000"), "25": Decimal("13000")}


def seed(apps, schema_editor):
    Grade = apps.get_model("catalog", "Grade")
    Packaging = apps.get_model("catalog", "Packaging")
    Product = apps.get_model("catalog", "Product")

    packs = {}
    for name, weight, wtag in PACKS:
        packs[wtag] = Packaging.objects.get_or_create(
            name=name, defaults={"weight_kg": weight, "is_active": True}
        )[0]

    for grade_name, color in COLORS:
        grade = Grade.objects.get_or_create(
            name=grade_name, defaults={"is_active": True}
        )[0]
        for wtag, pack in packs.items():
            cv = f"{color}_{wtag}"
            Product.objects.get_or_create(
                grade=grade, packaging=pack,
                defaults={"cv_class": cv, "price": PRICE[wtag], "is_active": True},
            )


def unseed(apps, schema_editor):
    # Удаляем только товары с нашими cv_class; справочники Grade/Packaging оставляем.
    Product = apps.get_model("catalog", "Product")
    codes = [f"{c}_{w}" for _, c in COLORS for w in ("50", "25")]
    Product.objects.filter(cv_class__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_product_cv_class"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
