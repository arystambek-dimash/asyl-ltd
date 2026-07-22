import pytest
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def _make_product(name="Премиум", color="Red", weight="50", price="100.00"):
    return Product.objects.create(name=name, color=color, weight_kg=weight, price=price)


def test_manager_creates_product(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/products/",
        {"name": "Премиум", "color": "Red", "weight_kg": "50"},
    )
    assert resp.status_code == 201
    product = Product.objects.get(name="Премиум", color="Red")
    assert product.price is None
    assert "price" not in resp.data


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
    assert "price" not in rows[prod.id]


def test_color_is_hidden_without_order_create_permission(
    auth_client, user_with_perms,
):
    viewer = user_with_perms("catalog-no-colors", codes=["catalog.view"])
    prod = _make_product(name="Скрытый цвет", color="Blue", weight="10")

    resp = auth_client(viewer).get("/api/products/")

    assert resp.status_code == 200
    row = next(item for item in resp.data if item["id"] == prod.id)
    assert row["label"] == "Скрытый цвет · 10 кг"
    assert "color" not in row
    assert "color_label" not in row
    assert "cv_class" not in row


def test_new_packaging_weights_are_supported(auth_client, manager):
    for weight in ("2", "5", "10"):
        resp = auth_client(manager).post(
            "/api/products/",
            {"name": f"Фасовка {weight}", "color": "Red", "weight_kg": weight},
        )
        assert resp.status_code == 201
        assert resp.data["cv_class"] == f"Red_{weight}"
