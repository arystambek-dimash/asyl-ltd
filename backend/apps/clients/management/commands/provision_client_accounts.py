import getpass
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Assign one temporary password to unprovisioned client accounts. "
        "The password is read securely and is never printed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the temporary password from one line on stdin.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many accounts are eligible without reading a password.",
        )

    @staticmethod
    def _targets(*, user_ids=None, lock=False):
        User = get_user_model()
        candidates = User._default_manager.filter(
            is_client=True,
            client_profile__isnull=False,
            must_change_password=True,
            is_active=False,
        )
        if user_ids is not None:
            candidates = candidates.filter(pk__in=user_ids)
        if lock:
            candidates = candidates.select_for_update()
        candidates = candidates.order_by("pk")
        return [user for user in candidates if not user.has_usable_password()]

    @staticmethod
    def _read_password(*, password_stdin: bool) -> str:
        if password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
            if not password:
                raise CommandError("No temporary password was provided on stdin.")
            return password

        password = getpass.getpass("Temporary client password: ")
        confirmation = getpass.getpass("Repeat temporary client password: ")
        if not password:
            raise CommandError("The temporary password must not be empty.")
        if password != confirmation:
            raise CommandError("The temporary passwords do not match.")
        return password

    @staticmethod
    def _validate_for_all(password: str, users) -> None:
        invalid = []
        for user in users:
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                invalid.append((user.username, exc.messages))

        if not invalid:
            return
        details = "; ".join(
            f"{username}: {', '.join(messages)}"
            for username, messages in invalid[:10]
        )
        if len(invalid) > 10:
            details += f"; and {len(invalid) - 10} more"
        raise CommandError(
            "The temporary password failed validation for client accounts: "
            + details
        )

    def handle(self, *args, **options):
        candidates = self._targets()
        count = len(candidates)
        if options["dry_run"]:
            self.stdout.write(f"Eligible client accounts: {count}")
            return
        if not candidates:
            self.stdout.write("No client accounts need provisioning.")
            return

        password = self._read_password(
            password_stdin=options["password_stdin"]
        )

        User = get_user_model()
        with transaction.atomic():
            users = self._targets(
                user_ids=[user.pk for user in candidates],
                lock=True,
            )
            if not users:
                self.stdout.write("No client accounts need provisioning.")
                return

            # Re-fetching under row locks makes concurrent command runs
            # idempotent. Validate every still-eligible account before the
            # first database write.
            self._validate_for_all(password, users)
            for user in users:
                # set_password generates an independent random salt for every
                # user, even though this one-time plaintext value is shared.
                user.set_password(password)
                user.is_active = True
                user.must_change_password = True
            User._default_manager.bulk_update(
                users,
                ["password", "is_active", "must_change_password"],
                batch_size=500,
            )

        self.stdout.write(
            self.style.SUCCESS(f"Provisioned client accounts: {len(users)}")
        )
