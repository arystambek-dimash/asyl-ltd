"""Сервисный пользователь бота: без входа, только права проведения отчётов."""
import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import connection

from apps.bots.models import TelegramBotSettings
from apps.bots.service_user import BOT_PERMISSION_CODES, BOT_USERNAME, ensure_bot_user
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def _codes(user):
    return set(user.employee.permissions.values_list("code", flat=True))


def test_bot_user_cannot_log_in_and_has_only_conduct_rights():
    user = ensure_bot_user()

    assert user.username == BOT_USERNAME
    assert not user.has_usable_password()
    assert (user.is_superuser, user.is_staff, user.is_client, user.is_active) == (False, False, False, True)
    assert _codes(user) == set(BOT_PERMISSION_CODES) == {
        "orders.create", "orders.confirm", "loader.confirm", "loader.wagons"}
    assert not user.has_perm_code("clients.set_price")
    assert user.employee.sales_department is None


def test_every_start_takes_away_what_was_added_in_employees(get_permission):
    user = ensure_bot_user()
    user.is_superuser = True
    user.set_password("guessable-password")
    user.save()
    user.employee.permissions.add(get_permission("clients.set_price"))
    user.employee.sales_department = Department.objects.create(code="retail", name="Розница")
    user.employee.is_active = False
    user.employee.save()

    ensure_bot_user()

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


# --- миграция 0010: WhatsApp-бот → Telegram-бот ---------------------------------------------------

MIGRATION = importlib.import_module("apps.bots.migrations.0010_telegram_bot")
SCHEMA_EDITOR = SimpleNamespace(connection=connection)


def test_whatsapp_bot_user_becomes_the_telegram_bot_and_keeps_its_orders():
    old = get_user_model().objects.create(username="whatsapp-bot", first_name="WhatsApp-бот")

    MIGRATION.rename_bot_user(django_apps, SCHEMA_EDITOR)

    old.refresh_from_db()
    assert (old.username, old.first_name) == (BOT_USERNAME, "Telegram-бот")
    assert ensure_bot_user().pk == old.pk


def test_the_owner_is_the_bots_first_user():
    """Решение владельца: ботом сразу пользуется @d1maaash — и в миграции, и в новой строке настроек."""
    assert TelegramBotSettings.objects.get().allowed_usernames == ["d1maaash"]
    TelegramBotSettings.objects.all().delete()

    assert TelegramBotSettings.load().allowed_usernames == ["d1maaash"]
