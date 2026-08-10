import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.permissions import IsAuthenticated

from apps.common.permissions import DenyAll, HasPerm, PermViewSetMixin
from apps.employees.models import Employee
from apps.sys_permissions.models import Permission

pytestmark = pytest.mark.django_db


class _Request:
    def __init__(self, user):
        self.user = user


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


def test_superuser_is_allowed(make_user):
    user = make_user(username="super-permissions")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    assert HasPerm("orders.create").has_permission(_Request(user), None) is True


def test_direct_employee_permission_grants_access(make_user):
    user = make_user(username="direct-permission")
    employee = Employee.objects.create(user=user, phone="x")
    employee.permissions.add(_permission("orders.view"))

    assert HasPerm("orders.view").has_permission(_Request(user), None) is True
    assert HasPerm("orders.create").has_permission(_Request(user), None) is False


def test_anonymous_user_is_denied():
    assert HasPerm("orders.view").has_permission(
        _Request(AnonymousUser()), None
    ) is False


def test_permission_mixin_fails_closed_for_unmapped_action(make_user):
    class UnmappedView(PermViewSetMixin):
        action = "new_action"
        required_perms = {}

    permission = UnmappedView().get_permissions()[0]
    assert isinstance(permission, DenyAll)
    assert permission.has_permission(_Request(make_user()), None) is False


def test_permission_mixin_preserves_unsupported_method_semantics():
    class UnsupportedMethodView(PermViewSetMixin):
        action = None

    assert isinstance(UnsupportedMethodView().get_permissions()[0], IsAuthenticated)
