import pytest
from decimal import Decimal
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def test_cv_class_computed_from_color_and_weight():
    p = Product(name="Высший", color="Red", weight_kg=Decimal("50"), price=Decimal("25000"))
    assert p.cv_class == "Red_50"
    p2 = Product(name="Высший", color="Blue", weight_kg=Decimal("25"), price=Decimal("13000"))
    assert p2.cv_class == "Blue_25"
    p3 = Product(name="Высший", color="Green", weight_kg=Decimal("5"))
    assert p3.cv_class == "Green_5"


def test_label_includes_name_color_weight():
    p = Product(name="Высший сорт", color="Green", weight_kg=Decimal("50"), price=Decimal("1"))
    assert str(p) == "Высший сорт · Зелёный 50 кг"
