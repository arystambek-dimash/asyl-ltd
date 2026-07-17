import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from apps.employees.models import Employee
from apps.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db
User = get_user_model()


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _perm(code):
    p, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": code.split(".")[0],
                             "action": code.split(".")[1], "label": code})
    return p


@pytest.fixture
def target(make_user):
    user = make_user(username="target")
    role = Role.objects.create(name="Роль-цель")
    role.permissions.add(_perm("orders.view"))
    return Employee.objects.create(
        user=user, first_name="Т", last_name="Т", phone="x", role=role)


def test_inactive_employee_loses_perm_codes(target):
    assert target.user.has_perm_code("orders.view")
    target.is_active = False
    target.save()
    user = User.objects.get(pk=target.user_id)  # без кэша effective_perm_codes
    assert not user.has_perm_code("orders.view")
    assert user.perm_codes == set()


def test_deactivation_via_api_disables_user_account(boss, target):
    r = _api(boss).patch(f"/api/employees/{target.id}/",
                         {"is_active": False}, format="json")
    assert r.status_code == 200
    target.user.refresh_from_db()
    # JWT-аутентификация отвергает неактивного пользователя — доступ закрыт сразу.
    assert target.user.is_active is False
    r = _api(boss).patch(f"/api/employees/{target.id}/",
                         {"is_active": True}, format="json")
    assert r.status_code == 200
    target.user.refresh_from_db()
    assert target.user.is_active is True


def test_destroy_employee_disables_user_account(boss, target):
    r = _api(boss).delete(f"/api/employees/{target.id}/")
    assert r.status_code == 204
    user = User.objects.get(pk=target.user_id)
    assert user.is_active is False
    assert not Employee.objects.filter(pk=target.id).exists()


def test_weak_password_rejected_on_create(boss):
    role = Role.objects.create(name="R")
    r = _api(boss).post("/api/employees/", {
        "username": "weak", "password": "123456",
        "first_name": "A", "last_name": "B", "phone": "x", "role": role.id,
    }, format="json")
    assert r.status_code == 400
    assert not User.objects.filter(username="weak").exists()
