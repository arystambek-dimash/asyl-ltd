import pytest
from rest_framework.exceptions import ValidationError
from apps.catalog.models import Product
from apps.catalog.services import archive_product, restore_product

pytestmark = pytest.mark.django_db


def _product(name="Премиум"):
    return Product.objects.create(name=name, color="Red", weight_kg="50", price="100.00")


def test_archive_sets_inactive(manager):
    p = _product()
    assert p.is_active is True
    archive_product(p, manager)
    p.refresh_from_db()
    assert p.is_active is False


def test_archive_twice_raises(manager):
    p = _product()
    archive_product(p, manager)
    with pytest.raises(ValidationError):
        archive_product(p, manager)


def test_restore_sets_active(manager):
    p = _product()
    archive_product(p, manager)
    restore_product(p, manager)
    p.refresh_from_db()
    assert p.is_active is True


def test_restore_active_raises(manager):
    p = _product()
    with pytest.raises(ValidationError):
        restore_product(p, manager)
