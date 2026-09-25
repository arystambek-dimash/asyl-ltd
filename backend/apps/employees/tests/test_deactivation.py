import pytest
from django.contrib.auth import get_user_model

from apps.employees.models import Employee
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def target(user_with_perms):
    return user_with_perms("target", codes=["orders.view"]).employee


def test_inactive_employee_loses_permission_codes(target):
    assert target.user.has_perm_code("orders.view")
    target.is_active = False
    target.save(update_fields=["is_active"])
    user = User.objects.get(pk=target.user_id)
    assert not user.has_perm_code("orders.view")
    assert user.perm_codes == set()


def test_deactivation_via_security_endpoint_disables_user(auth_client, boss, target):
    response = auth_client(boss).patch(
        f"/api/employees/{target.id}/security/",
        {"is_active": False},
        format="json",
    )
    assert response.status_code == 200
    target.user.refresh_from_db()
    assert target.user.is_active is False


def test_destroy_employee_disables_user_account(auth_client, boss, target):
    response = auth_client(boss).delete(f"/api/employees/{target.id}/")
    assert response.status_code == 204
    user = User.objects.get(pk=target.user_id)
    assert user.is_active is False
    assert not Employee.objects.filter(pk=target.id).exists()


def test_weak_password_is_rejected_on_create(auth_client, boss):
    response = auth_client(boss).post(
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


def test_employee_flag_switches_login_on_plain_save(target):
    # Админка сохраняет сотрудника целиком, без update_fields.
    target.is_active = False
    target.save()
    assert User.objects.get(pk=target.user_id).is_active is False

    target.is_active = True
    target.save()
    assert User.objects.get(pk=target.user_id).is_active is True


def test_deactivation_in_admin_disables_login(admin_client, target):
    response = admin_client.post(
        f"/admin/employees/employee/{target.id}/change/",
        {
            "user": target.user_id,
            "phone": target.phone,
            "position": "",
            "sales_department": "",
            "permissions": [p.pk for p in target.permissions.all()],
            "first_name": "Имя",
            "last_name": "Фамилия",
            # is_active не передан: галочка снята.
        },
    )

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.is_active is False
    assert User.objects.get(pk=target.user_id).is_active is False


def test_password_change_ignores_username_from_body(auth_client, boss, target):
    response = auth_client(boss).post(
        f"/api/employees/{target.id}/password/",
        {"password": "Another-safe-pass-2026!", "username": "boss"},
        format="json",
    )

    assert response.status_code == 204
    user = User.objects.get(pk=target.user_id)
    assert user.username == "target"
    event = EventLog.objects.filter(event_type="employee_security").latest("id")
    assert event.message == "Изменён пароль сотрудника target"


def test_password_change_compares_with_real_username(auth_client, boss, target):
    # Подставленный логин не должен обходить проверку сходства с настоящим.
    response = auth_client(boss).post(
        f"/api/employees/{target.id}/password/",
        {"password": "targetxyz", "username": "someone-else"},
        format="json",
    )

    assert response.status_code == 400
    assert not User.objects.get(pk=target.user_id).check_password("targetxyz")
