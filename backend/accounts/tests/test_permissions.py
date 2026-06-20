import pytest
from accounts.permissions import IsBoss, IsStaff, IsClientUser

pytestmark = pytest.mark.django_db


class _Req:
    def __init__(self, user):
        self.user = user


def test_is_boss_allows_boss(boss):
    assert IsBoss().has_permission(_Req(boss), None) is True


def test_is_boss_denies_manager(manager):
    assert IsBoss().has_permission(_Req(manager), None) is False


def test_is_staff_denies_client(client_user):
    assert IsStaff().has_permission(_Req(client_user), None) is False


def test_is_client_user_allows_client(client_user):
    assert IsClientUser().has_permission(_Req(client_user), None) is True
