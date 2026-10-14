import importlib

import pytest
from django.apps import apps as django_apps

from apps.dashboard.models import DashboardSettings
from apps.sales.models import Department
from apps.warehouse.models import Warehouse
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db


def test_superuser_chooses_departments_and_warehouses(auth_client, make_user, user_with_perms):
    city = Department.objects.create(code="city", name="Нью-Сити")
    second = Warehouse.objects.create(code="second", name="Мельница 2")
    root = make_user(username="dashboard-root")
    root.is_superuser = True
    root.save(update_fields=["is_superuser"])

    response = auth_client(root).put(
        "/api/dashboard/settings/", {"departments": [city.pk], "warehouses": [second.pk]}, format="json"
    )

    assert response.status_code == 200, response.data
    assert response.data["departments"] == [{"id": city.pk, "code": "city", "name": "Нью-Сити"}]
    assert response.data["warehouses"] == [{"id": second.pk, "name": "Мельница 2"}]
    viewer = auth_client(user_with_perms("dashboard-viewer", codes=["dashboard.view"]))
    assert viewer.get("/api/dashboard/settings/").data["warehouses"] == [{"id": second.pk, "name": "Мельница 2"}]


def test_only_superuser_changes_what_the_dashboard_shows(auth_client, user_with_perms):
    viewer = auth_client(user_with_perms("dashboard-editor", codes=["dashboard.view", "sys_permissions.manage"]))

    assert viewer.put("/api/dashboard/settings/", {"departments": [], "warehouses": []}, format="json").status_code == 403
    assert auth_client(user_with_perms("no-dashboard", codes=["orders.view"])).get(
        "/api/dashboard/settings/"
    ).status_code == 403


def test_migration_starts_with_the_default_department_and_warehouse():
    """После выката «Главная» показывает основной отдел и основной склад — «Мельницу»."""
    Department.objects.update(is_default=False)
    mill = Department.objects.create(code="mill", name="Мельница", is_default=True)
    Department.objects.create(code="city", name="Нью-Сити")
    Warehouse.objects.create(code="second", name="Мельница 2")
    migration = importlib.import_module("apps.dashboard.migrations.0002_default_scope")

    migration.show_default_department_and_warehouse(django_apps, None)

    row = DashboardSettings.load()
    assert list(row.departments.all()) == [mill]
    assert list(row.warehouses.all()) == [get_default_warehouse()]
