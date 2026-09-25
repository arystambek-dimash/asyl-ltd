import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.permissions import IsAuthenticated

from apps.common.permissions import DenyAll, HasPerm, PermViewSetMixin

pytestmark = pytest.mark.django_db


class _Request:
    def __init__(self, user):
        self.user = user


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
