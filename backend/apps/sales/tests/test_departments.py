import pytest
from django.utils import timezone

from apps.clients.models import Client
from apps.orders.models import Order
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def test_staff_lists_active_departments(operator, api_as):
    Department.objects.update_or_create(
        code="main", defaults={"name": "Оптовый", "is_default": True}
    )
    Department.objects.create(code="hidden", name="Старый", is_active=False)

    response = api_as(operator).get("/api/departments/")

    assert response.status_code == 200
    names = [row["name"] for row in response.data]
    assert "Оптовый" in names
    assert "Старый" not in names


def test_admin_creates_and_renames_dynamic_department(boss, api_as):
    response = api_as(boss).post(
        "/api/departments/",
        {"name": "Региональные продажи", "color": "#238C6E"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["code"].startswith("department-")
    assert response.data["is_default"] is False

    response = api_as(boss).patch(
        f"/api/departments/{response.data['id']}/",
        {"name": "Регионы", "color": "#D68B2C"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["name"] == "Регионы"
    assert response.data["color"] == "#D68B2C"


def test_regular_staff_cannot_manage_departments(operator, api_as):
    response = api_as(operator).post(
        "/api/departments/",
        {"name": "Нельзя", "color": "#315FD5"},
        format="json",
    )
    assert response.status_code == 403


def test_department_name_is_unique_case_insensitive(boss, api_as):
    Department.objects.update_or_create(
        code="main", defaults={"name": "Оптовый", "is_default": True}
    )
    response = api_as(boss).post(
        "/api/departments/",
        {"name": "  оптовый ", "color": "#315FD5"},
        format="json",
    )
    assert response.status_code == 400


def test_department_order_counts_follow_client_ownership(user_with_perms, api_as):
    owner_a = Department.objects.create(code="count-owner-a", name="Владельцы A")
    owner_b = Department.objects.create(code="count-owner-b", name="Владельцы B")
    order_department = Department.objects.create(
        code="count-order-bucket",
        name="Направление заказов",
    )
    client_a = Client.objects.create_with_user(
        first_name="Свой клиент",
        phone="count-a",
        department=owner_a,
    )
    client_b = Client.objects.create_with_user(
        first_name="Чужой клиент",
        phone="count-b",
        department=owner_b,
    )
    Order.objects.create(client=client_a, department=order_department.code)
    Order.objects.create(client=client_b, department=order_department.code)
    assigned = user_with_perms("department-count-owner-a", codes=[], department=owner_a)

    response = api_as(assigned).get("/api/departments/")

    assert response.status_code == 200
    row = next(
        item for item in response.data if item["id"] == order_department.pk
    )
    assert row["order_count"] == 1


def test_department_order_count_skips_trashed_orders(user_with_perms, api_as):
    department = Department.objects.create(code="count-trash", name="Корзина")
    client = Client.objects.create_with_user(first_name="Клиент", phone="count-trash")
    Order.objects.create(client=client, department=department.code)
    trashed = Order.objects.create(client=client, department=department.code)
    Order.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
    viewer = user_with_perms("department-count-trash", codes=[])

    response = api_as(viewer).get("/api/departments/")

    assert response.status_code == 200
    row = next(item for item in response.data if item["id"] == department.pk)
    assert row["order_count"] == 1
