import pytest
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def test_superuser_can_create_product(auth_client, make_user):
    admin = make_user(username="admin")
    admin.is_superuser = True
    admin.is_staff = True
    admin.save()
    resp = auth_client(admin).post(
        "/api/products/",
        {"name": "Премиум", "color": "Red", "weight_kg": "50", "price": "25000"},
    )
    assert resp.status_code == 201
    assert Product.objects.filter(name="Премиум", color="Red").exists()
