import pytest
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def test_manager_creates_product(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/products/",
        {"name": "Премиум", "color": "Red", "weight_kg": "50"},
    )
    assert resp.status_code == 201
    assert Product.objects.filter(name="Премиум", color="Red").exists()


def test_operator_cannot_create_product(auth_client, operator):
    resp = auth_client(operator).post(
        "/api/products/",
        {"name": "X", "color": "Blue", "weight_kg": "25"},
    )
    assert resp.status_code == 403


def test_staff_can_list_products(auth_client, manager, make_product):
    prod = make_product(name="Эталон")
    resp = auth_client(manager).get("/api/products/")
    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.data}
    assert prod.id in rows
    assert rows[prod.id]["color_label"] == "Красный"


def test_color_is_hidden_without_order_create_permission(
    auth_client, user_with_perms, make_product,
):
    viewer = user_with_perms("catalog-no-colors", codes=["catalog.view"])
    prod = make_product(name="Скрытый цвет", color="Blue", weight_kg="10")

    resp = auth_client(viewer).get("/api/products/")

    assert resp.status_code == 200
    row = next(item for item in resp.data if item["id"] == prod.id)
    assert row["label"] == "Скрытый цвет · 10 кг"
    assert "color" not in row
    assert "color_label" not in row


def test_new_packaging_weights_are_supported(auth_client, manager):
    for weight in ("2", "5", "10"):
        resp = auth_client(manager).post(
            "/api/products/",
            {"name": f"Фасовка {weight}", "color": "Red", "weight_kg": weight},
        )
        assert resp.status_code == 201
        assert resp.data["label"] == f"Фасовка {weight} · Красный {weight} кг"


def test_warehouse_adjuster_lists_products_with_colors(auth_client, user_with_perms, make_product):
    keeper = user_with_perms("warehouse-keeper", codes=["warehouse.view", "warehouse.adjust"])
    prod = make_product(name="Для склада", color="Blue", weight_kg="25")

    resp = auth_client(keeper).get("/api/products/")

    assert resp.status_code == 200
    row = next(item for item in resp.data if item["id"] == prod.id)
    assert row["label"] == "Для склада · Синий 25 кг"
    assert auth_client(keeper).post(
        "/api/products/", {"name": "X", "color": "Red", "weight_kg": "50"}
    ).status_code == 403


def test_warehouse_viewer_cannot_list_products(auth_client, user_with_perms):
    viewer = user_with_perms("warehouse-viewer", codes=["warehouse.view"])

    assert auth_client(viewer).get("/api/products/").status_code == 403
