"""Названия отделов продаж: видят все сотрудники, переименовывает админ."""
import pytest
from rest_framework.test import APIClient
from apps.clients.models import Department

pytestmark = pytest.mark.django_db


def _api(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def _dept(code="field", name="Сити"):
    d, _ = Department.objects.get_or_create(code=code, defaults={"name": name})
    return d


def test_any_staff_sees_department_names(operator):
    _dept("main", "Отдел 1")
    _dept("field", "Сити")
    r = _api(operator).get("/api/departments/")
    assert r.status_code == 200
    assert {row["code"] for row in r.data} == {"main", "field"}


def _superadmin(make_user):
    u = make_user(username="root")
    u.is_superuser = True
    u.save()
    return u


def test_superadmin_renames_department(make_user):
    admin = _superadmin(make_user)
    d = _dept("field", "Сити")
    r = _api(admin).patch(f"/api/departments/{d.id}/", {"name": "Выездной отдел"}, format="json")
    assert r.status_code == 200
    d.refresh_from_db()
    assert d.name == "Выездной отдел"


def test_rename_requires_superuser(operator, user_with_perms):
    d = _dept()
    # Обычному сотруднику нельзя.
    r = _api(operator).patch(f"/api/departments/{d.id}/", {"name": "Хак"}, format="json")
    assert r.status_code == 403
    # Даже держателю rbac.manage (не суперадмину) — нельзя.
    manager = user_with_perms("rbacman", codes=["rbac.manage"])
    r = _api(manager).patch(f"/api/departments/{d.id}/", {"name": "Хак"}, format="json")
    assert r.status_code == 403


def test_code_is_immutable(make_user):
    admin = _superadmin(make_user)
    d = _dept("field", "Сити")
    r = _api(admin).patch(f"/api/departments/{d.id}/", {"code": "hacked", "name": "X"}, format="json")
    assert r.status_code == 200
    d.refresh_from_db()
    assert d.code == "field"


def test_me_returns_department_names(auth_client, operator):
    _dept("main", "Отдел 1")
    _dept("field", "Сити")
    r = auth_client(operator).get("/api/auth/me/")
    assert r.status_code == 200
    assert r.data["department_names"]["field"] == "Сити"
