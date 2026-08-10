import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.employees.models import Employee
from apps.sys_permissions.models import Permission

pytestmark = pytest.mark.django_db
User = get_user_model()


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _permission(code):
    permission, _ = Permission.objects.get_or_create(
        code=code,
        defaults={
            "section": code.split(".")[0],
            "action": code.split(".")[1],
            "label": code,
        },
    )
    return permission


@pytest.fixture
def target(make_user):
    user = make_user(username="target")
    employee = Employee.objects.create(user=user, phone="x")
    employee.permissions.add(_permission("orders.view"))
    return employee


def test_inactive_employee_loses_permission_codes(target):
    assert target.user.has_perm_code("orders.view")
    target.is_active = False
    target.save(update_fields=["is_active"])
    user = User.objects.get(pk=target.user_id)
    assert not user.has_perm_code("orders.view")
    assert user.perm_codes == set()


def test_deactivation_via_security_endpoint_disables_user(boss, target):
    response = _api(boss).patch(
        f"/api/employees/{target.id}/security/",
        {"is_active": False},
        format="json",
    )
    assert response.status_code == 200
    target.user.refresh_from_db()
    assert target.user.is_active is False


def test_destroy_employee_disables_user_account(boss, target):
    response = _api(boss).delete(f"/api/employees/{target.id}/")
    assert response.status_code == 204
    user = User.objects.get(pk=target.user_id)
    assert user.is_active is False
    assert not Employee.objects.filter(pk=target.id).exists()


def test_weak_password_is_rejected_on_create(boss):
    response = _api(boss).post(
        "/api/employees/",
        {
            "username": "weak",
            "password": "123456",
            "first_name": "A",
            "last_name": "B",
            "phone": "x",
        },
        format="json",
    )
    assert response.status_code == 400
    assert not User.objects.filter(username="weak").exists()
