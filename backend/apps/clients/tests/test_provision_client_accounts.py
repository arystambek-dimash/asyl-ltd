import sys
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.clients.models import Client

pytestmark = pytest.mark.django_db


def _unprovisioned_client(first_name, last_name):
    return Client.objects.create_with_user(
        first_name=first_name,
        last_name=last_name,
        phone="+7",
    )


def test_command_provisions_only_unprovisioned_client_accounts(
    monkeypatch,
    make_user,
):
    first = _unprovisioned_client("Account", "One")
    second = _unprovisioned_client("Account", "Two")
    existing_user = make_user(
        username="existing-portal-client",
        password="Existing-portal-pass-2026!",
        client=True,
    )
    Client.objects.create_with_user(
        user=existing_user,
        first_name="Existing",
        last_name="Client",
        phone="+7",
    )
    existing_hash = existing_user.password
    inactive_existing_user = make_user(
        username="inactive-existing-portal-client",
        password="Inactive-existing-pass-2026!",
        client=True,
    )
    inactive_existing_user.is_active = False
    inactive_existing_user.must_change_password = True
    inactive_existing_user.save(
        update_fields=["is_active", "must_change_password"]
    )
    Client.objects.create_with_user(
        user=inactive_existing_user,
        first_name="Inactive",
        last_name="Existing",
        phone="+7",
    )
    inactive_existing_hash = inactive_existing_user.password
    password = "Shared-temporary-pass-2026!"
    monkeypatch.setattr(sys, "stdin", StringIO(password + "\n"))
    output = StringIO()

    call_command(
        "provision_client_accounts",
        password_stdin=True,
        stdout=output,
    )

    first.user.refresh_from_db()
    second.user.refresh_from_db()
    existing_user.refresh_from_db()
    inactive_existing_user.refresh_from_db()
    for user in (first.user, second.user):
        assert user.is_active is True
        assert user.must_change_password is True
        assert user.check_password(password)
    assert first.user.password != second.user.password
    assert existing_user.password == existing_hash
    assert existing_user.check_password("Existing-portal-pass-2026!")
    assert inactive_existing_user.is_active is False
    assert inactive_existing_user.password == inactive_existing_hash
    assert inactive_existing_user.check_password("Inactive-existing-pass-2026!")
    assert password not in output.getvalue()
    assert "Provisioned client accounts: 2" in output.getvalue()


def test_command_is_idempotent_and_does_not_read_another_password(monkeypatch):
    client = _unprovisioned_client("Idempotent", "Client")
    password = "Shared-temporary-pass-2026!"
    monkeypatch.setattr(sys, "stdin", StringIO(password + "\n"))
    call_command("provision_client_accounts", password_stdin=True)
    client.user.refresh_from_db()
    provisioned_hash = client.user.password

    unused_input = StringIO("Different-temporary-pass-2026!\n")
    monkeypatch.setattr(sys, "stdin", unused_input)
    output = StringIO()
    call_command(
        "provision_client_accounts",
        password_stdin=True,
        stdout=output,
    )

    client.user.refresh_from_db()
    assert client.user.password == provisioned_hash
    assert unused_input.tell() == 0
    assert "No client accounts need provisioning." in output.getvalue()


def test_dry_run_does_not_prompt_or_write(monkeypatch):
    client = _unprovisioned_client("Dry", "Run")

    def unexpected_prompt(*args, **kwargs):
        raise AssertionError("dry-run must not request a password")

    monkeypatch.setattr(
        "apps.clients.management.commands.provision_client_accounts.getpass.getpass",
        unexpected_prompt,
    )
    output = StringIO()

    call_command("provision_client_accounts", dry_run=True, stdout=output)

    client.user.refresh_from_db()
    assert client.user.is_active is False
    assert client.user.has_usable_password() is False
    assert "Eligible client accounts: 1" in output.getvalue()


def test_password_is_validated_for_every_target_before_any_write(monkeypatch):
    first = _unprovisioned_client("Weak", "One")
    second = _unprovisioned_client("Weak", "Two")
    monkeypatch.setattr(sys, "stdin", StringIO("12345678\n"))

    with pytest.raises(CommandError, match="failed validation"):
        call_command("provision_client_accounts", password_stdin=True)

    first.user.refresh_from_db()
    second.user.refresh_from_db()
    for user in (first.user, second.user):
        assert user.is_active is False
        assert user.has_usable_password() is False


def test_interactive_password_must_be_confirmed(monkeypatch):
    client = _unprovisioned_client("Interactive", "Client")
    answers = iter(("First-temporary-pass-2026!", "Other-pass-2026!"))
    monkeypatch.setattr(
        "apps.clients.management.commands.provision_client_accounts.getpass.getpass",
        lambda _prompt: next(answers),
    )

    with pytest.raises(CommandError, match="do not match"):
        call_command("provision_client_accounts")

    client.user.refresh_from_db()
    assert client.user.is_active is False
    assert client.user.has_usable_password() is False
