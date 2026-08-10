import pytest

from apps.employees.models import Employee
from apps.sys_permissions.models import Permission

pytestmark = pytest.mark.django_db


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


def test_superuser_has_any_code(make_user):
    user = make_user(username="super-codes")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    assert user.has_perm_code("orders.create") is True


def test_employee_permissions_grant_codes(make_user):
    user = make_user(username="employee-codes")
    employee = Employee.objects.create(user=user, phone="x")
    employee.permissions.add(_permission("orders.view"))

    assert user.has_perm_code("orders.view") is True
    assert user.has_perm_code("orders.create") is False
    assert user.perm_codes == {"orders.view"}


def test_employee_without_direct_permissions_has_no_codes(make_user):
    user = make_user(username="employee-no-codes")
    Employee.objects.create(user=user, phone="x")
    assert user.perm_codes == set()


def test_user_without_employee_has_no_codes(make_user):
    user = make_user(username="user-no-employee")
    assert user.perm_codes == set()
    assert user.has_perm_code("orders.view") is False
