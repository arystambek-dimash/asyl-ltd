import pytest
from apps.rbac.permissions import HasPerm
from apps.rbac.models import Permission, Role

pytestmark = pytest.mark.django_db


class _Req:
    def __init__(self, user):
        self.user = user


def test_superuser_allowed(make_user):
    u = make_user(username="su"); u.is_superuser = True; u.save()
    assert HasPerm("orders.create").has_permission(_Req(u), None) is True


def test_user_with_code_allowed(make_user):
    from apps.employees.models import Employee
    u = make_user(username="m")
    role = Role.objects.create(name="R")
    p, _ = Permission.objects.get_or_create(code="orders.view", defaults={"section":"orders","action":"view","label":"x"})
    role.permissions.add(p)
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    assert HasPerm("orders.view").has_permission(_Req(u), None) is True
    assert HasPerm("orders.create").has_permission(_Req(u), None) is False


def test_anon_denied():
    from django.contrib.auth.models import AnonymousUser
    assert HasPerm("orders.view").has_permission(_Req(AnonymousUser()), None) is False
