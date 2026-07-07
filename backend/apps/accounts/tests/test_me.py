import pytest

pytestmark = pytest.mark.django_db


def test_me_returns_permissions(auth_client, make_user):
    from apps.rbac.models import Permission, Role
    from apps.employees.models import Employee
    u = make_user(username="m")
    role = Role.objects.create(name="R")
    p, _ = Permission.objects.get_or_create(
        code="orders.view", defaults={"section": "orders", "action": "view", "label": "x"})
    emp = Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    # Права выданы сотруднику персонально; роль в ответе — лишь назначение.
    emp.permissions.add(p)
    resp = auth_client(u).get("/api/auth/me/")
    assert resp.status_code == 200
    assert "orders.view" in resp.data["permissions"]
    assert resp.data["role_name"] == "R"


def test_me_for_client_includes_client_id(auth_client, client_user):
    from apps.clients.models import Client
    c = Client.objects.create(first_name="Мой", last_name="К", phone="x", user=client_user)
    resp = auth_client(client_user).get("/api/auth/me/")
    assert resp.status_code == 200
    assert resp.data["is_client"] is True
    assert resp.data["client_id"] == c.id


def test_me_requires_auth(api_client):
    resp = api_client.get("/api/auth/me/")
    assert resp.status_code == 401
