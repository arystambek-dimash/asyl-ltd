from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.employees.models import Employee
from apps.eventlog.models import EventLog
from apps.sales.models import Department
from apps.sys_permissions.models import Permission

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def admin_client(auth_client, make_user):
    user = make_user(username="employee-root")
    user.is_superuser = True
    user.is_staff = True
    user.save(update_fields=["is_superuser", "is_staff"])
    return auth_client(user)


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


def _create_employee(client, **overrides):
    payload = {
        "username": "ivan",
        "password": "A-safe-pass-2026!",
        "first_name": "Иван",
        "last_name": "Петров",
        "phone": "+7700",
        "position": "Кладовщик",
    }
    payload.update(overrides)
    return client.post("/api/employees/", payload, format="json")


def test_admin_creates_employee_and_user(admin_client):
    response = _create_employee(admin_client)

    assert response.status_code == 201
    user = User.objects.get(username="ivan")
    employee = Employee.objects.get(user=user)
    assert user.check_password("A-safe-pass-2026!")
    assert user.first_name == "Иван"
    assert user.last_name == "Петров"
    assert employee.position == "Кладовщик"
    assert response.data["first_name"] == "Иван"
    assert response.data["last_name"] == "Петров"
    assert response.data["name"] == "Иван Петров"
    assert "password" not in response.data
    assert "role" not in response.data


def test_password_is_required_on_create(admin_client):
    response = _create_employee(admin_client, password=None)
    assert response.status_code == 400


def test_create_with_direct_permissions(admin_client):
    _permission("warehouse.view")
    _permission("warehouse.adjust")
    response = _create_employee(
        admin_client,
        username="anna",
        permission_codes=["warehouse.view", "warehouse.adjust"],
    )

    assert response.status_code == 201
    assert response.data["permissions"] == ["warehouse.adjust", "warehouse.view"]
    user = User.objects.get(username="anna")
    assert user.has_perm_code("warehouse.view") is True
    assert user.has_perm_code("orders.view") is False


def test_profile_security_and_password_have_separate_endpoints(admin_client):
    _permission("clients.view")
    response = _create_employee(admin_client, username="petr")
    employee_id = response.data["id"]

    profile = admin_client.patch(
        f"/api/employees/{employee_id}/",
        {"first_name": "Пётр"},
        format="json",
    )
    security = admin_client.patch(
        f"/api/employees/{employee_id}/security/",
        {"username": "petr-new", "permission_codes": ["clients.view"]},
        format="json",
    )
    password = admin_client.post(
        f"/api/employees/{employee_id}/password/",
        {"password": "Another-safe-pass-2026!"},
        format="json",
    )

    assert profile.status_code == 200
    assert security.status_code == 200
    assert password.status_code == 204
    user = User.objects.get(username="petr-new")
    assert user.check_password("Another-safe-pass-2026!")
    assert user.first_name == "Пётр"
    assert user.last_name == "Петров"
    assert user.has_perm_code("clients.view") is True
    assert EventLog.objects.filter(
        event_type="employee_profile",
        payload__employee_id=employee_id,
    ).exists()
    event = EventLog.objects.filter(event_type="employee_security").latest("id")
    assert event.payload["password_changed"] is True
    assert "Another-safe-pass-2026!" not in str(event.payload)


def test_security_change_rolls_back_when_audit_log_fails(admin_client):
    permission = _permission("clients.view")
    department = Department.objects.create(
        code="atomic-department",
        name="Атомарный отдел",
    )
    response = _create_employee(admin_client, username="atomic-security")
    employee = Employee.objects.get(pk=response.data["id"])

    with patch(
        "apps.employees.views.log_event",
        side_effect=RuntimeError("audit unavailable"),
    ), pytest.raises(RuntimeError, match="audit unavailable"):
        admin_client.patch(
            f"/api/employees/{employee.pk}/security/",
            {
                "username": "atomic-security-changed",
                "permission_codes": [permission.code],
                "is_active": False,
                "sales_department": department.pk,
            },
            format="json",
        )

    employee.refresh_from_db()
    employee.user.refresh_from_db()
    assert employee.user.username == "atomic-security"
    assert employee.is_active is True
    assert employee.user.is_active is True
    assert employee.sales_department_id is None
    assert not employee.permissions.exists()


def test_password_change_rolls_back_when_audit_log_fails(admin_client):
    response = _create_employee(admin_client, username="atomic-password")
    employee = Employee.objects.get(pk=response.data["id"])

    with patch(
        "apps.employees.views.log_event",
        side_effect=RuntimeError("audit unavailable"),
    ), pytest.raises(RuntimeError, match="audit unavailable"):
        admin_client.post(
            f"/api/employees/{employee.pk}/password/",
            {"password": "Changed-safe-pass-2026!"},
            format="json",
        )

    employee.user.refresh_from_db()
    assert employee.user.check_password("A-safe-pass-2026!")
    assert not employee.user.check_password("Changed-safe-pass-2026!")


def test_profile_update_rejects_security_fields(admin_client):
    department = Department.objects.create(
        code="protected-department",
        name="Защищённый отдел",
    )
    response = _create_employee(admin_client, username="profile-only")
    employee = Employee.objects.get(pk=response.data["id"])

    response = admin_client.patch(
        f"/api/employees/{employee.pk}/",
        {
            "first_name": "Не сохранить",
            "is_active": False,
            "sales_department": department.pk,
        },
        format="json",
    )

    assert response.status_code == 400
    employee.user.refresh_from_db()
    employee.refresh_from_db()
    assert employee.user.first_name == "Иван"
    assert employee.is_active is True
    assert employee.sales_department_id is None


def test_sales_department_changes_through_security_endpoint(admin_client):
    department = Department.objects.create(
        code="secure-sales",
        name="Безопасный отдел",
    )
    response = _create_employee(admin_client, username="secure-department")
    employee = Employee.objects.get(pk=response.data["id"])

    response = admin_client.patch(
        f"/api/employees/{employee.pk}/security/",
        {"sales_department": department.pk},
        format="json",
    )

    assert response.status_code == 200
    employee.refresh_from_db()
    assert employee.sales_department_id == department.pk
    event = EventLog.objects.filter(event_type="employee_security").latest("id")
    assert event.payload["before"]["sales_department_id"] is None
    assert event.payload["after"]["sales_department_id"] == department.pk


def test_full_profile_update_does_not_require_create_credentials(admin_client):
    response = _create_employee(admin_client, username="full-profile-update")
    employee = Employee.objects.get(pk=response.data["id"])

    response = admin_client.put(
        f"/api/employees/{employee.pk}/",
        {
            "first_name": "Новое",
            "last_name": "Имя",
            "phone": "+7701",
            "position": "Оператор",
        },
        format="json",
    )

    assert response.status_code == 200
    employee.user.refresh_from_db()
    assert employee.user.username == "full-profile-update"
    assert employee.user.first_name == "Новое"
    assert employee.user.last_name == "Имя"


def test_employee_manager_can_edit_profile_but_not_security(
    auth_client,
    user_with_perms,
    make_user,
):
    manager = user_with_perms(
        "profile-manager", codes=["employees.view", "employees.manage"]
    )
    target_user = make_user(username="managed-user")
    target_user.first_name = "До"
    target_user.last_name = "Проверки"
    target_user.save(update_fields=["first_name", "last_name"])
    target = Employee.objects.create(user=target_user, phone="+7")
    client = auth_client(manager)

    profile = client.patch(
        f"/api/employees/{target.pk}/",
        {"first_name": "После"},
        format="json",
    )
    security = client.patch(
        f"/api/employees/{target.pk}/security/",
        {"permission_codes": ["sys_permissions.manage"]},
        format="json",
    )
    password = client.post(
        f"/api/employees/{target.pk}/password/",
        {"password": "Another-safe-pass-2026!"},
        format="json",
    )

    assert profile.status_code == 200
    assert security.status_code == 403
    assert password.status_code == 403
    target_user.refresh_from_db()
    assert target_user.first_name == "После"
    assert target_user.last_name == "Проверки"
    assert not target.permissions.exists()


def test_permission_manager_cannot_grant_permission_they_do_not_have(
    auth_client,
    user_with_perms,
    make_user,
):
    manager = user_with_perms(
        "permission-manager",
        codes=[
            "employees.view",
            "employees.manage",
            "sys_permissions.view",
            "sys_permissions.manage",
        ],
    )
    _permission("payments.confirm")
    target_user = make_user(username="permission-target")
    target = Employee.objects.create(user=target_user, phone="x")

    response = auth_client(manager).patch(
        f"/api/employees/{target.pk}/security/",
        {"permission_codes": ["payments.confirm"]},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "perm_escalation"
    assert not target.permissions.exists()


def test_permission_manager_cannot_reset_more_privileged_account(
    auth_client,
    user_with_perms,
    make_user,
):
    manager = user_with_perms(
        "password-manager",
        codes=[
            "employees.manage",
            "sys_permissions.manage",
        ],
    )
    target_user = make_user(
        username="privileged-target",
        password="Original-safe-pass-2026!",
    )
    target = Employee.objects.create(user=target_user, phone="x")
    target.permissions.add(_permission("payments.confirm"))

    response = auth_client(manager).post(
        f"/api/employees/{target.pk}/password/",
        {"password": "Captured-safe-pass-2026!"},
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["code"] == "privileged_target_forbidden"
    target_user.refresh_from_db()
    assert target_user.check_password("Original-safe-pass-2026!")
    assert not target_user.check_password("Captured-safe-pass-2026!")


def test_permission_manager_cannot_change_own_security(
    auth_client,
    user_with_perms,
):
    manager = user_with_perms(
        "self-manager",
        codes=[
            "employees.manage",
            "sys_permissions.manage",
        ],
    )

    response = auth_client(manager).patch(
        f"/api/employees/{manager.employee.pk}/security/",
        {"permission_codes": []},
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["code"] == "self_security_change_forbidden"
    assert manager.has_perm_code("employees.manage")


def test_permission_manager_cannot_reset_django_staff_account(
    auth_client,
    user_with_perms,
    make_user,
):
    manager = user_with_perms(
        "staff-password-manager",
        codes=["employees.manage", "sys_permissions.manage"],
    )
    staff_user = make_user(
        username="django-staff-target",
        password="Original-safe-pass-2026!",
    )
    staff_user.is_staff = True
    staff_user.save(update_fields=["is_staff"])
    target = Employee.objects.create(user=staff_user, phone="x")

    response = auth_client(manager).post(
        f"/api/employees/{target.pk}/password/",
        {"password": "Captured-safe-pass-2026!"},
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["code"] == "staff_target_forbidden"
    staff_user.refresh_from_db()
    assert staff_user.check_password("Original-safe-pass-2026!")


def test_sales_department_does_not_grant_permissions(admin_client):
    department = Department.objects.create(
        code="sales-north", name="Север", color="#315FD5", is_default=True
    )
    response = _create_employee(
        admin_client,
        username="sales",
        sales_department=department.id,
    )

    assert response.status_code == 201
    assert response.data["sales_department_name"] == "Север"
    user = User.objects.get(username="sales")
    assert user.perm_codes == set()


def test_inactive_sales_department_cannot_be_assigned(admin_client):
    department = Department.objects.create(
        code="closed-sales", name="Закрытый", is_active=False
    )
    response = _create_employee(
        admin_client,
        username="closed",
        sales_department=department.id,
    )
    assert response.status_code == 400
    assert "sales_department" in response.data["detail"]
