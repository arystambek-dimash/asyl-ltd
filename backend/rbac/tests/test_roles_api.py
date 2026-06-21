import pytest
from rbac.models import Role, Permission

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.save()
    return auth_client(u)


def test_permissions_list(admin_client):
    resp = admin_client.get("/api/permissions/")
    assert resp.status_code == 200
    assert any(p["code"] == "orders.create" for p in resp.data)


def test_create_role_with_codes(admin_client):
    resp = admin_client.post("/api/roles/", {
        "name": "Кладовщик", "permission_codes": ["warehouse.view", "warehouse.adjust"],
    }, format="json")
    assert resp.status_code == 201
    role = Role.objects.get(name="Кладовщик")
    assert set(role.permissions.values_list("code", flat=True)) == {"warehouse.view", "warehouse.adjust"}


def test_system_role_cannot_be_deleted(admin_client):
    r = Role.objects.create(name="Сис-роль", is_system=True)
    resp = admin_client.delete(f"/api/roles/{r.id}/")
    assert resp.status_code == 400


def test_role_with_employees_cannot_be_deleted(admin_client, make_user):
    from employees.models import Employee
    r = Role.objects.create(name="Темп")
    u = make_user(username="emp1")
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=r)
    resp = admin_client.delete(f"/api/roles/{r.id}/")
    assert resp.status_code == 400
