import pytest

pytestmark = pytest.mark.django_db


def test_me_returns_permissions(auth_client, user_with_perms):
    u = user_with_perms("m", codes=["orders.view"])
    u.employee.position = "Оператор"
    u.employee.save(update_fields=["position"])
    resp = auth_client(u).get("/api/auth/me/")
    assert resp.status_code == 200
    assert "orders.view" in resp.data["permissions"]
    assert resp.data["position"] == "Оператор"


def test_me_marks_client(auth_client, client_user):
    from apps.clients.models import Client
    Client.objects.create_with_user(phone="x", user=client_user)
    resp = auth_client(client_user).get("/api/auth/me/")
    assert resp.status_code == 200
    assert resp.data["is_client"] is True


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
    assert response.data["permissions"] == []


def test_me_hides_department_from_superuser(auth_client, admin_user):
    """Суперюзер не ограничен отделом (sales.access), даже если он указан в карточке."""
    from apps.sales.models import Department
    from apps.employees.models import Employee

    department = Department.objects.create(code="sales-west", name="Запад", is_default=True)
    Employee.objects.create(user=admin_user, sales_department=department)

    response = auth_client(admin_user).get("/api/auth/me/")

    assert response.status_code == 200
    assert response.data["sales_department"] is None
