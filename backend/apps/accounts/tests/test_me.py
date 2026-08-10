import pytest

pytestmark = pytest.mark.django_db


def test_me_returns_permissions(auth_client, make_user):
    from apps.employees.models import Employee
    from apps.sys_permissions.models import Permission
    u = make_user(username="m")
    p, _ = Permission.objects.get_or_create(
        code="orders.view", defaults={"section": "orders", "action": "view", "label": "x"})
    emp = Employee.objects.create(user=u, phone="x", position="Оператор")
    emp.permissions.add(p)
    resp = auth_client(u).get("/api/auth/me/")
    assert resp.status_code == 200
    assert "orders.view" in resp.data["permissions"]
    assert resp.data["position"] == "Оператор"


def test_me_for_client_includes_client_id(auth_client, client_user):
    from apps.clients.models import Client
    client_user.first_name = "Мой"
    client_user.last_name = "К"
    client_user.save(update_fields=["first_name", "last_name"])
    c = Client.objects.create_with_user(phone="x", user=client_user)
    resp = auth_client(client_user).get("/api/auth/me/")
    assert resp.status_code == 200
    assert resp.data["is_client"] is True
    assert resp.data["client_id"] == c.id


def test_me_exposes_name_from_user(auth_client, make_user):
    user = make_user(username="named-user")
    user.first_name = "Айжан"
    user.last_name = "Серикова"
    user.save(update_fields=["first_name", "last_name"])

    response = auth_client(user).get("/api/auth/me/")

    assert response.status_code == 200
    assert response.data["first_name"] == "Айжан"
    assert response.data["last_name"] == "Серикова"


def test_me_requires_auth(api_client):
    resp = api_client.get("/api/auth/me/")
    assert resp.status_code == 401


def test_me_exposes_employee_sales_department(auth_client, make_user):
    from apps.sales.models import Department
    from apps.employees.models import Employee

    department = Department.objects.create(
        code="sales-west", name="Запад", color="#D68B2C", is_default=True)
    user = make_user(username="sales-west-user")
    Employee.objects.create(user=user, sales_department=department)

    response = auth_client(user).get("/api/auth/me/")

    assert response.status_code == 200
    assert response.data["sales_department"] == {
        "id": department.id,
        "code": "sales-west",
        "name": "Запад",
        "color": "#D68B2C",
    }
    assert "orders.create" not in response.data["permissions"]
