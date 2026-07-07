import pytest
from apps.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


def _perm(code):
    p, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": code.split(".")[0],
                             "action": code.split(".")[1], "label": code})
    return p


def test_superuser_has_any_code(make_user):
    u = make_user(username="su")
    u.is_superuser = True
    u.save()
    assert u.has_perm_code("orders.create") is True


def test_employee_permissions_grant_code(make_user):
    from apps.employees.models import Employee
    u = make_user(username="e1")
    emp = Employee.objects.create(user=u, first_name="A", last_name="B", phone="x")
    emp.permissions.add(_perm("orders.view"))
    assert u.has_perm_code("orders.view") is True
    assert u.has_perm_code("orders.create") is False
    assert "orders.view" in u.perm_codes


def test_role_alone_grants_nothing(make_user):
    """Роль — назначение: её права не действуют, пока не выданы сотруднику."""
    from apps.employees.models import Employee
    u = make_user(username="e3")
    role = Role.objects.create(name="R")
    role.permissions.add(_perm("orders.view"))
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    assert u.has_perm_code("orders.view") is False
    assert u.perm_codes == set()


def test_no_employee_no_codes(make_user):
    u = make_user(username="e2")
    assert u.perm_codes == set()
    assert u.has_perm_code("orders.view") is False
