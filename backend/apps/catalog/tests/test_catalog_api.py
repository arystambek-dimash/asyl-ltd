import pytest
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def _make_product(name="Премиум", color="Red", weight="50", price="100.00"):
    return Product.objects.create(name=name, color=color, weight_kg=weight, price=price)


def test_manager_creates_product(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/products/",
        {"name": "Премиум", "color": "Red", "weight_kg": "50", "price": "25000"},
    )
    assert resp.status_code == 201
    assert Product.objects.filter(name="Премиум", color="Red").exists()


def test_operator_cannot_create_product(auth_client, operator):
    resp = auth_client(operator).post(
        "/api/products/",
        {"name": "X", "color": "Blue", "weight_kg": "25", "price": "1"},
    )
    assert resp.status_code == 403


def test_product_label_and_cv_class(auth_client, manager):
    prod = _make_product()
    assert str(prod) == "Премиум · Красный 50 кг"
    assert prod.cv_class == "Red_50"


def test_staff_can_list_products(auth_client, manager):
    prod = _make_product(name="Эталон")
    resp = auth_client(manager).get("/api/products/")
    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.data}
    assert prod.id in rows
    assert rows[prod.id]["color_label"] == "Красный"
    assert rows[prod.id]["cv_class"] == "Red_50"
