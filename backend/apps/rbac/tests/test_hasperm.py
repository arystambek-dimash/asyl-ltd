import pytest
from apps.common.permissions import DenyAll, HasPerm, PermViewSetMixin
from apps.rbac.models import Permission
from rest_framework.permissions import IsAuthenticated

pytestmark = pytest.mark.django_db


class _Req:
    def __init__(self, user):
        self.user = user


def test_superuser_allowed(make_user):
    u = make_user(username="su")
    u.is_superuser = True
    u.save()
    assert HasPerm("orders.create").has_permission(_Req(u), None) is True


def test_user_with_code_allowed(make_user):
    from apps.employees.models import Employee

    u = make_user(username="m")
    p, _ = Permission.objects.get_or_create(
        code="orders.view",
        defaults={"section": "orders", "action": "view", "label": "x"},
    )
    emp = Employee.objects.create(user=u, first_name="A", last_name="B", phone="x")
    emp.permissions.add(p)
    assert HasPerm("orders.view").has_permission(_Req(u), None) is True
    assert HasPerm("orders.create").has_permission(_Req(u), None) is False


def test_role_permission_grants_access(make_user):
    """Право, выданное роли, действует на сотрудника без личной копии."""
    from apps.employees.models import Employee
    from apps.rbac.models import Role

    u = make_user(username="r")
    p, _ = Permission.objects.get_or_create(
        code="orders.view",
        defaults={"section": "orders", "action": "view", "label": "x"},
    )
    role = Role.objects.create(name="Тест-оператор")
    role.permissions.add(p)
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    assert HasPerm("orders.view").has_permission(_Req(u), None) is True
    assert u.perm_codes == {"orders.view"}


def test_role_edit_applies_immediately(make_user):
    """Правка прав роли сразу меняет доступы всех её сотрудников."""
    from django.contrib.auth import get_user_model
    from apps.employees.models import Employee
    from apps.rbac.models import Role

    u = make_user(username="live")
    p, _ = Permission.objects.get_or_create(
        code="clients.view",
        defaults={"section": "clients", "action": "view", "label": "x"},
    )
    role = Role.objects.create(name="Тест-менеджер")
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    assert HasPerm("clients.view").has_permission(_Req(u), None) is False
    role.permissions.add(p)
    u = get_user_model().objects.get(pk=u.pk)  # свежий запрос — как новый HTTP-запрос
    assert HasPerm("clients.view").has_permission(_Req(u), None) is True


def test_anon_denied():
    from django.contrib.auth.models import AnonymousUser

    assert HasPerm("orders.view").has_permission(_Req(AnonymousUser()), None) is False


def test_permission_mixin_fails_closed_for_unmapped_action(make_user):
    class UnmappedView(PermViewSetMixin):
        action = "new_action"
        required_perms = {}

    permission = UnmappedView().get_permissions()[0]

    assert isinstance(permission, DenyAll)
    assert permission.has_permission(_Req(make_user()), None) is False


def test_permission_mixin_preserves_unsupported_method_semantics():
    class UnsupportedMethodView(PermViewSetMixin):
        action = None

    permission = UnsupportedMethodView().get_permissions()[0]

    assert isinstance(permission, IsAuthenticated)
