import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product

pytestmark = pytest.mark.django_db


def test_existing_products_get_new_fields_after_save():
    # Simulate a pre-migration product (grade+packaging+cv_class_old), then
    # re-run the same transform logic the data migration applies.
    g = Grade.objects.create(name="ТестКрасный")
    pk = Packaging.objects.create(name="ТестМешок 50 кг", weight_kg="50.00")
    p = Product.objects.create(grade=g, packaging=pk, price="25000",
                               cv_class_old="Red_50")
    p.name = p.grade.name
    p.new_weight_kg = p.packaging.weight_kg
    p.color = p.cv_class_old.split("_")[0]
    p.save()
    p.refresh_from_db()
    assert p.name == "ТестКрасный"
    assert p.new_weight_kg == Decimal("50.00")
    assert p.color == "Red"
    assert p.cv_class == "Red_50"
