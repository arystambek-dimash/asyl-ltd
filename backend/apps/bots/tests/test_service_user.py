"""Сервисный пользователь бота: без входа, только права проведения отчётов."""
import pytest
from django.core.management import call_command

from apps.bots.service_user import BOT_PERMISSION_CODES, BOT_USERNAME, ensure_bot_user
from apps.sales.models import Department
from apps.sys_permissions.models import Permission

pytestmark = pytest.mark.django_db


def _codes(user):
    return set(user.employee.permissions.values_list("code", flat=True))


def test_bot_user_cannot_log_in_and_has_only_conduct_rights():
    user = ensure_bot_user()

    assert user.username == BOT_USERNAME
    assert not user.has_usable_password()
    assert (user.is_superuser, user.is_staff, user.is_client, user.is_active) == (False, False, False, True)
    assert _codes(user) == set(BOT_PERMISSION_CODES) == {
        "orders.create", "orders.confirm", "loader.view", "loader.confirm", "loader.wagons"}
    assert not user.has_perm_code("clients.set_price")
    assert user.employee.sales_department is None


def test_every_start_takes_away_what_was_added_in_employees():
    user = ensure_bot_user()
    user.is_superuser = True
    user.set_password("guessable-password")
    user.save()
    user.employee.permissions.add(Permission.objects.get_or_create(
        code="clients.set_price", defaults={"section": "clients", "action": "set_price", "label": "Цены"})[0])
    user.employee.sales_department = Department.objects.create(code="retail", name="Розница")
    user.employee.is_active = False
    user.employee.save()

    call_command("ensure_whatsapp_bot_user")

    user = type(user).objects.get(pk=user.pk)
    assert (user.is_superuser, user.has_usable_password(), user.employee.is_active) == (False, False, True)
    assert user.employee.sales_department is None
    assert "clients.set_price" not in _codes(user)
    assert not user.has_perm_code("clients.set_price")
    assert _codes(user) == set(BOT_PERMISSION_CODES)


def test_ensure_is_idempotent():
    first = ensure_bot_user()
    second = ensure_bot_user()

    assert first.pk == second.pk
    assert type(first).objects.filter(username=BOT_USERNAME).count() == 1
