import pytest
from apps.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


def _role_with(*codes):
    role = Role.objects.create(name="R")
    for c in codes:
        p, _ = Permission.objects.get_or_create(
            code=c, defaults={"section": c.split(".")[0], "action": c.split(".")[1], "label": c})
        role.permissions.add(p)
    return role


def test_superuser_has_any_code(make_user):
    u = make_user(username="su")
    u.is_superuser = True
    u.save()
    assert u.has_perm_code("orders.create") is True


def test_employee_role_grants_code(make_user):
    from apps.employees.models import Employee
    u = make_user(username="e1")
    role = _role_with("orders.view")
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    assert u.has_perm_code("orders.view") is True
    assert u.has_perm_code("orders.create") is False
    assert "orders.view" in u.perm_codes


def test_no_employee_no_codes(make_user):
    u = make_user(username="e2")
    assert u.perm_codes == set()
    assert u.has_perm_code("orders.view") is False
