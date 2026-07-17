import pytest
from rest_framework.test import APIClient

from apps.clients.models import Department

pytestmark = pytest.mark.django_db


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_staff_lists_active_departments(operator):
    Department.objects.update_or_create(
        code="main", defaults={"name": "Оптовый", "is_default": True})
    Department.objects.create(code="hidden", name="Старый", is_active=False)

    response = _api(operator).get("/api/departments/")

    assert response.status_code == 200
    names = [row["name"] for row in response.data]
    assert "Оптовый" in names
    assert "Старый" not in names


def test_admin_creates_and_renames_dynamic_department(boss):
    response = _api(boss).post(
        "/api/departments/", {"name": "Региональные продажи", "color": "#238C6E"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["code"].startswith("department-")
    assert response.data["is_default"] is False

    response = _api(boss).patch(
        f"/api/departments/{response.data['id']}/",
        {"name": "Регионы", "color": "#D68B2C"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["name"] == "Регионы"
    assert response.data["color"] == "#D68B2C"


def test_regular_staff_cannot_manage_departments(operator):
    response = _api(operator).post(
        "/api/departments/", {"name": "Нельзя", "color": "#315FD5"}, format="json")
    assert response.status_code == 403


def test_department_name_is_unique_case_insensitive(boss):
    Department.objects.update_or_create(
        code="main", defaults={"name": "Оптовый", "is_default": True})
    response = _api(boss).post(
        "/api/departments/", {"name": "  оптовый ", "color": "#315FD5"}, format="json")
    assert response.status_code == 400
