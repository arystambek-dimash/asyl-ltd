import pytest
from catalog.models import Grade, Packaging, Product

pytestmark = pytest.mark.django_db


def _make_product(price="100.00"):
    g = Grade.objects.create(name="Премиум")
    p = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    return Product.objects.create(grade=g, packaging=p, price=price)


def test_manager_creates_grade(auth_client, manager):
    resp = auth_client(manager).post("/api/grades/", {"name": "Премиум"})
    assert resp.status_code == 201
    assert Grade.objects.filter(name="Премиум").exists()


def test_operator_cannot_create_grade(auth_client, operator):
    resp = auth_client(operator).post("/api/grades/", {"name": "X"})
    assert resp.status_code == 403


def test_product_weight_from_packaging(auth_client, manager):
    prod = _make_product()
    assert str(prod) == "Премиум 50 кг"
    assert prod.weight_kg == prod.packaging.weight_kg


def test_staff_can_list_products(auth_client, manager):
    _make_product()
    resp = auth_client(manager).get("/api/products/")
    assert resp.status_code == 200
    assert len(resp.data) == 1
