import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def _set_command_env(monkeypatch, *, password, reset=False):
    monkeypatch.setenv("SUPER_ADMIN_EMAIL", "bootstrap@example.com")
    monkeypatch.setenv("SUPER_ADMIN_PASS", password)
    monkeypatch.setenv("SUPER_ADMIN_RESET_PASSWORD", "1" if reset else "0")


def test_command_rejects_placeholder_password(monkeypatch, django_user_model):
    _set_command_env(monkeypatch, password="change-me")

    with pytest.raises(CommandError, match="шаблонным"):
        call_command("create_superuser_env")

    assert not django_user_model.objects.filter(
        username="bootstrap@example.com"
    ).exists()


def test_command_rejects_password_failing_django_validators(
    monkeypatch,
    django_user_model,
):
    user = django_user_model.objects.create_user(
        username="bootstrap@example.com",
        email="bootstrap@example.com",
        password="Original-strong-pass-2026!",
    )
    original_hash = user.password
    _set_command_env(monkeypatch, password="12345678", reset=True)

    with pytest.raises(CommandError, match="Django password validators"):
        call_command("create_superuser_env")

    user.refresh_from_db()
    assert user.password == original_hash
    assert user.is_superuser is False
    assert user.is_staff is False


def test_command_creates_superuser_with_valid_password(
    monkeypatch,
    django_user_model,
):
    password = "Unique-bootstrap-pass-2026!"
    _set_command_env(monkeypatch, password=password)

    call_command("create_superuser_env")

    user = django_user_model.objects.get(username="bootstrap@example.com")
    assert user.email == "bootstrap@example.com"
    assert user.is_superuser is True
    assert user.is_staff is True
    assert user.check_password(password)


def test_existing_superuser_is_a_noop_even_if_legacy_env_password_is_weak(
    monkeypatch,
    django_user_model,
):
    user = django_user_model.objects.create_superuser(
        username="bootstrap@example.com",
        email="bootstrap@example.com",
        password="Existing-secure-pass-2026!",
    )
    original_hash = user.password
    _set_command_env(monkeypatch, password="change-me", reset=False)

    call_command("create_superuser_env")

    user.refresh_from_db()
    assert user.password == original_hash
