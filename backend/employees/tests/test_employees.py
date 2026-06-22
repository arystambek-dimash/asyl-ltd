import pytest
from django.contrib.auth import get_user_model
from rbac.models import Role
from employees.models import Employee

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def admin_client(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.is_staff = True; u.save()
    return auth_client(u)


def test_admin_creates_employee_with_account(admin_client):
    role = Role.objects.create(name="Тест-роль")
    resp = admin_client.post("/api/employees/", {
        "username": "ivan", "password": "pass12345",
        "first_name": "Иван", "last_name": "Петров", "phone": "+7700",
        "position": "Кладовщик", "role": role.id,
    }, format="json")
    assert resp.status_code == 201
    u = User.objects.get(username="ivan")
    assert u.check_password("pass12345")
    assert Employee.objects.get(user=u).role_id == role.id
    assert "password" not in resp.data


def test_password_required_on_create(admin_client):
    role = Role.objects.create(name="R")
    resp = admin_client.post("/api/employees/", {
        "username": "x", "first_name": "A", "last_name": "B",
        "phone": "y", "role": role.id,
    }, format="json")
    assert resp.status_code == 400


def test_non_admin_without_perm_denied(auth_client, make_user):
    u = make_user(username="plain")
    role = Role.objects.create(name="R")
    resp = auth_client(u).post("/api/employees/", {
        "username": "z", "password": "pass12345", "first_name": "A",
        "last_name": "B", "phone": "y", "role": role.id,
    }, format="json")
    assert resp.status_code == 403
