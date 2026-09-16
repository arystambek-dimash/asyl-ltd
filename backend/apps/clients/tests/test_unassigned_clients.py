import pytest

from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def departments():
    mill = Department.objects.create(code="mill", name="Мельница")
    city = Department.objects.create(code="city", name="Нью-Сити")
    return mill, city


@pytest.fixture
def mill_cashier(user_with_perms, departments):
    # Пресет «Касса»: clients.edit нет, забирает клиента через orders.confirm.
    user = user_with_perms("mill-cashier", codes=["clients.view", "orders.view", "orders.confirm"])
    user.employee.sales_department = departments[0]
    user.employee.save(update_fields=["sales_department"])
    return user


def _client(name, department=None):
    return Client.objects.create_with_user(first_name=name, phone="+7 (705) 565-65-65", department=department)


def _ids(response):
    return {row["id"] for row in response.data}


def test_department_sees_unassigned_clients_but_not_other_departments(auth_client, mill_cashier, departments):
    mill, city = departments
    own = _client("Свой", mill)
    foreign = _client("Чужой", city)
    waiting = _client("Новый")
    api = auth_client(mill_cashier)

    assert _ids(api.get("/api/clients/?department=none")) == {waiting.pk}
    assert _ids(api.get("/api/clients/")) == {own.pk}
    assert api.get(f"/api/clients/{waiting.pk}/").status_code == 200
    assert api.get(f"/api/clients/{foreign.pk}/").status_code == 404


def test_cashier_takes_unassigned_client_into_own_department(auth_client, mill_cashier, departments):
    waiting = _client("Новый")

    response = auth_client(mill_cashier).post(f"/api/clients/{waiting.pk}/assign-department/", {}, format="json")

    assert response.status_code == 200
    assert response.data["department"] == departments[0].pk
    waiting.refresh_from_db()
    assert waiting.department == departments[0]
    event = EventLog.objects.get(payload__action="client_department_changed", payload__client_id=waiting.pk)
    assert event.payload["department_from"] is None
    assert event.payload["department_to"] == "mill"


def test_cashier_cannot_assign_client_to_another_department(auth_client, mill_cashier, departments):
    waiting = _client("Новый")

    response = auth_client(mill_cashier).post(
        f"/api/clients/{waiting.pk}/assign-department/", {"department": departments[1].pk}, format="json"
    )

    assert response.status_code == 403
    waiting.refresh_from_db()
    assert waiting.department is None


def test_assigned_client_is_not_taken_over(auth_client, user_with_perms, departments):
    mill, city = departments
    admin = user_with_perms("admin-no-department", codes=["clients.view", "clients.edit"])
    owned = _client("Свой", mill)

    response = auth_client(admin).post(
        f"/api/clients/{owned.pk}/assign-department/", {"department": city.pk}, format="json"
    )

    assert response.status_code == 400
    assert response.data["code"] == "client_already_assigned"
    owned.refresh_from_db()
    assert owned.department == mill


def test_employee_without_department_must_choose_one(auth_client, user_with_perms, departments):
    admin = user_with_perms("admin-no-department", codes=["clients.view", "clients.edit"])
    waiting = _client("Новый")
    api = auth_client(admin)

    assert api.post(f"/api/clients/{waiting.pk}/assign-department/", {}, format="json").status_code == 400
    response = api.post(
        f"/api/clients/{waiting.pk}/assign-department/", {"department": departments[1].pk}, format="json"
    )

    assert response.status_code == 200
    waiting.refresh_from_db()
    assert waiting.department == departments[1]


def test_assign_requires_review_permission(auth_client, user_with_perms):
    viewer = user_with_perms("viewer", codes=["clients.view"])
    waiting = _client("Новый")

    response = auth_client(viewer).post(f"/api/clients/{waiting.pk}/assign-department/", {}, format="json")

    assert response.status_code == 403
