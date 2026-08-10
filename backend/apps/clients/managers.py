from itertools import count

from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.utils.text import slugify


def _normalized_name(value) -> str:
    return " ".join(str(value or "").split())


def _username_candidate(base: str, number: int, max_length: int) -> str:
    suffix = "" if number == 1 else f"-{number}"
    stem = base[: max_length - len(suffix)].rstrip("-._")
    return f"{stem or 'client'}{suffix}"


class ClientManager(models.Manager):
    """Create the Client/User aggregate without exposing a half-created profile."""

    def _create_portal_user(self, first_name: str, last_name: str):
        User = get_user_model()
        users = User._default_manager.db_manager(self.db)
        max_length = User._meta.get_field("username").max_length
        base = slugify(
            " ".join(part for part in (first_name, last_name) if part),
            allow_unicode=True,
        ) or "client"

        for number in count(1):
            username = _username_candidate(base, number, max_length)
            if users.filter(username__iexact=username).exists():
                continue
            try:
                # The savepoint keeps a concurrent username collision from
                # poisoning the aggregate's outer transaction.
                with transaction.atomic(using=self.db):
                    return users.create_user(
                        username=username,
                        password=None,
                        first_name=first_name,
                        last_name=last_name,
                        is_client=True,
                        is_active=False,
                        must_change_password=True,
                    )
            except IntegrityError:
                continue

    def create_with_user(
        self,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        user=None,
        **fields,
    ):
        """Create a client and its portal identity as one transaction.

        Staff-created clients receive a disabled account with an unusable
        password. An administrator explicitly enables access by assigning a
        temporary password through the client password endpoint.
        """
        if user is not None:
            if first_name is None:
                first_name = user.first_name
            if last_name is None:
                last_name = user.last_name

        first_name = _normalized_name(first_name)
        last_name = _normalized_name(last_name)
        if not first_name:
            raise ValueError("Client first_name must not be blank")

        with transaction.atomic(using=self.db):
            if user is None:
                user = self._create_portal_user(first_name, last_name)
            else:
                if user.pk is None:
                    raise ValueError("Client user must be saved")
                if not user.is_client:
                    raise ValueError("Client user must have is_client=True")
                if user.is_staff or user.is_superuser:
                    raise ValueError("Client user must not be a staff account")
                if getattr(user, "employee", None) is not None:
                    raise ValueError("Client user must not have an employee profile")
                update_fields = []
                for field, value in (
                    ("first_name", first_name),
                    ("last_name", last_name),
                ):
                    if getattr(user, field) != value:
                        setattr(user, field, value)
                        update_fields.append(field)
                if update_fields:
                    user.save(using=self.db, update_fields=update_fields)

            return self.create(user=user, **fields)
