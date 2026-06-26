import pytest
from apps.rbac.models import Role

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.save()
    return auth_client(u)


def test_permissions_list(admin_client):
    resp = admin_client.get("/api/permissions/")
    assert resp.status_code == 200
    assert any(p["code"] == "orders.create" for p in resp.data)


def test_permissions_list_requires_rbac_view(auth_client, user_with_perms):
    employees_user = user_with_perms("employees-view", codes=["employees.view"])
    resp = auth_client(employees_user).get("/api/permissions/")
    assert resp.status_code == 403

    rbac_user = user_with_perms("rbac-view", codes=["rbac.view"])
    resp = auth_client(rbac_user).get("/api/permissions/")
    assert resp.status_code == 200


def test_create_role_with_codes(admin_client):
    resp = admin_client.post("/api/roles/", {
        "name": "Кладовщик", "permission_codes": ["warehouse.view", "warehouse.adjust"],
    }, format="json")
    assert resp.status_code == 201
    role = Role.objects.get(name="Кладовщик")
    assert set(role.permissions.values_list("code", flat=True)) == {"warehouse.view", "warehouse.adjust"}


def test_create_role_requires_rbac_manage(auth_client, user_with_perms):
    viewer = user_with_perms("roles-viewer", codes=["rbac.view"])
    resp = auth_client(viewer).post("/api/roles/", {
        "name": "Недоступно", "permission_codes": ["warehouse.view"],
    }, format="json")
    assert resp.status_code == 403

    manager = user_with_perms("roles-manager", codes=["rbac.manage"])
    resp = auth_client(manager).post("/api/roles/", {
        "name": "Управляющий доступами", "permission_codes": ["warehouse.view"],
    }, format="json")
    assert resp.status_code == 201


def test_create_role_rejects_unknown_permission_code(admin_client):
    resp = admin_client.post("/api/roles/", {
        "name": "Неверная роль", "permission_codes": ["warehouse.view", "missing.code"],
    }, format="json")
    assert resp.status_code == 400
    assert not Role.objects.filter(name="Неверная роль").exists()


def test_system_role_without_employees_can_be_deleted(admin_client):
    r = Role.objects.create(name="Сис-роль", is_system=True)
    resp = admin_client.delete(f"/api/roles/{r.id}/")
    assert resp.status_code == 204
    assert not Role.objects.filter(id=r.id).exists()


def test_system_role_with_employees_cannot_be_deleted(admin_client, make_user):
    from apps.employees.models import Employee
    r = Role.objects.create(name="Сис-роль-2", is_system=True)
    u = make_user(username="emp_sys")
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=r)
    resp = admin_client.delete(f"/api/roles/{r.id}/")
    assert resp.status_code == 400


def test_role_with_employees_cannot_be_deleted(admin_client, make_user):
    from apps.employees.models import Employee
    r = Role.objects.create(name="Темп")
    u = make_user(username="emp1")
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=r)
    resp = admin_client.delete(f"/api/roles/{r.id}/")
    assert resp.status_code == 400


def test_boss_preset_has_management_permissions():
    role = Role.objects.get(name="Начальник")
    codes = set(role.permissions.values_list("code", flat=True))
    assert {"employees.view", "employees.manage", "rbac.view", "rbac.manage"} <= codes
