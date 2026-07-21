import pytest
from django.contrib.auth import get_user_model
from apps.rbac.models import Role
from apps.employees.models import Employee

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


def _perm(code):
    from apps.rbac.models import Permission
    p, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": code.split(".")[0],
                             "action": code.split(".")[1], "label": code})
    return p


def test_create_with_explicit_permissions(admin_client):
    """Личные доступы добавляются поверх прав роли."""
    role = Role.objects.create(name="Кладовщик")
    role.permissions.add(_perm("orders.view"))
    resp = admin_client.post("/api/employees/", {
        "username": "anna", "password": "pass12345",
        "first_name": "Анна", "last_name": "С", "phone": "+7",
        "role": role.id,
        "permission_codes": ["warehouse.view", "warehouse.adjust"],
    }, format="json")
    assert resp.status_code == 201
    assert resp.data["permissions"] == ["warehouse.adjust", "warehouse.view"]
    assert resp.data["role_permissions"] == ["orders.view"]
    u = User.objects.get(username="anna")
    assert u.has_perm_code("warehouse.view") is True
    assert u.has_perm_code("orders.view") is True  # унаследовано от роли


def test_create_without_permissions_inherits_role(admin_client):
    """Права роли действуют без копирования: личный список пуст."""
    role = Role.objects.create(name="Оператор-2")
    role.permissions.add(_perm("orders.view"))
    resp = admin_client.post("/api/employees/", {
        "username": "oleg", "password": "pass12345",
        "first_name": "Олег", "last_name": "К", "phone": "+7", "role": role.id,
    }, format="json")
    assert resp.status_code == 201
    assert resp.data["permissions"] == []
    assert User.objects.get(username="oleg").has_perm_code("orders.view") is True


def test_inherited_permission_can_be_denied_for_one_employee(admin_client):
    role = Role.objects.create(name="Менеджер-гибкий")
    role.permissions.add(_perm("catalog.view"), _perm("catalog.delete"))
    response = admin_client.post("/api/employees/", {
        "username": "limited", "password": "pass12345",
        "first_name": "Лимит", "last_name": "Тест", "phone": "+7",
        "role": role.id,
        "denied_permission_codes": ["catalog.delete"],
    }, format="json")

    assert response.status_code == 201
    assert response.data["denied_permissions"] == ["catalog.delete"]
    user = User.objects.get(username="limited")
    assert user.has_perm_code("catalog.view") is True
    assert user.has_perm_code("catalog.delete") is False


def test_update_permissions_and_password(admin_client):
    role = Role.objects.create(name="R2")
    resp = admin_client.post("/api/employees/", {
        "username": "petr", "password": "pass12345",
        "first_name": "Пётр", "last_name": "В", "phone": "+7", "role": role.id,
    }, format="json")
    emp_id = resp.data["id"]
    resp = admin_client.patch(f"/api/employees/{emp_id}/", {
        "username": "petr", "permission_codes": ["clients.view"],
        "password": "newpass123",
    }, format="json")
    assert resp.status_code == 200
    u = User.objects.get(username="petr")
    assert u.check_password("newpass123")
    assert u.has_perm_code("clients.view") is True


def test_role_change_switches_inherited_permissions(admin_client):
    """Смена роли меняет наследуемые доступы, личные не трогает."""
    r1 = Role.objects.create(name="Р1")
    r1.permissions.add(_perm("orders.view"))
    r2 = Role.objects.create(name="Р2")
    r2.permissions.add(_perm("clients.view"))
    resp = admin_client.post("/api/employees/", {
        "username": "vera", "password": "pass12345",
        "first_name": "Вера", "last_name": "Д", "phone": "+7", "role": r1.id,
        "permission_codes": ["warehouse.view"],
    }, format="json")
    emp_id = resp.data["id"]
    resp = admin_client.patch(f"/api/employees/{emp_id}/",
                              {"username": "vera", "role": r2.id}, format="json")
    assert resp.status_code == 200
    u = User.objects.get(username="vera")
    assert u.has_perm_code("orders.view") is False   # права старой роли ушли
    assert u.has_perm_code("clients.view") is True   # права новой роли действуют
    assert u.has_perm_code("warehouse.view") is True  # личное право осталось
