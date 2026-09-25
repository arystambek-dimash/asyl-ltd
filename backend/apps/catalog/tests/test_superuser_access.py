import pytest
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def test_superuser_can_create_product(auth_client, admin_user):
    resp = auth_client(admin_user).post(
        "/api/products/",
        {"name": "Премиум", "color": "Red", "weight_kg": "50"},
    )
    assert resp.status_code == 201
    assert Product.objects.filter(name="Премиум", color="Red").exists()
