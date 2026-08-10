import unicodedata

import django.db.models.deletion
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
from django.utils.text import slugify


USERNAME_MAX_LENGTH = 150


def _generated_username(client, used_usernames):
    full_name = unicodedata.normalize(
        "NFKC", f"{client.first_name} {client.last_name}".strip()
    )
    stem = slugify(full_name, allow_unicode=True).strip("-") or "client"
    client_suffix = f"-{client.pk}"
    collision_number = 1

    while True:
        collision_suffix = (
            "" if collision_number == 1 else f"-{collision_number}"
        )
        available = (
            USERNAME_MAX_LENGTH
            - len(client_suffix)
            - len(collision_suffix)
        )
        shortened_stem = stem[:available].strip("-") or "client"[:available]
        candidate = f"{shortened_stem}{client_suffix}{collision_suffix}"
        normalized_candidate = candidate.casefold()
        if normalized_candidate not in used_usernames:
            used_usernames.add(normalized_candidate)
            return candidate
        collision_number += 1


def move_names_to_user(apps, schema_editor):
    Client = apps.get_model("clients", "Client")
    Employee = apps.get_model("employees", "Employee")
    User = apps.get_model("accounts", "User")
    database = schema_editor.connection.alias

    clients = list(
        Client.objects.using(database)
        .select_related("user")
        .only(
            "id",
            "user_id",
            "first_name",
            "last_name",
            "user__id",
            "user__first_name",
            "user__last_name",
            "user__is_client",
            "user__is_staff",
            "user__is_superuser",
        )
        .order_by("id")
    )

    linked_user_ids = {
        client.user_id for client in clients if client.user_id is not None
    }
    employee_user_ids = set(
        Employee.objects.using(database)
        .filter(user_id__in=linked_user_ids)
        .values_list("user_id", flat=True)
    )

    conflicts = []
    invalid_accounts = []
    for client in clients:
        if client.user_id is None:
            continue
        user = client.user
        for field in ("first_name", "last_name"):
            client_value = getattr(client, field)
            user_value = getattr(user, field)
            if user_value and user_value != client_value:
                conflicts.append(
                    f"client={client.pk}/user={client.user_id}/{field}"
                )

        invalid_flags = []
        if not user.is_client:
            invalid_flags.append("is_client=False")
        if user.is_staff:
            invalid_flags.append("is_staff=True")
        if user.is_superuser:
            invalid_flags.append("is_superuser=True")
        if user.pk in employee_user_ids:
            invalid_flags.append("employee=True")
        if invalid_flags:
            invalid_accounts.append(
                f"client={client.pk}/user={client.user_id}/"
                + ",".join(invalid_flags)
            )

    if conflicts:
        details = ", ".join(conflicts[:20])
        if len(conflicts) > 20:
            details += f", and {len(conflicts) - 20} more"
        raise RuntimeError(
            "Client/User name conflicts detected; resolve them before migration: "
            + details
        )

    if invalid_accounts:
        details = ", ".join(invalid_accounts[:20])
        if len(invalid_accounts) > 20:
            details += f", and {len(invalid_accounts) - 20} more"
        raise RuntimeError(
            "Client is linked to an account with incompatible role flags; "
            "resolve it before migration: "
            + details
        )

    used_usernames = {
        username.casefold()
        for username in User.objects.using(database).values_list(
            "username", flat=True
        )
    }
    linked_users = []
    clients_to_link = []

    for client in clients:
        if client.user_id is not None:
            client.user.first_name = client.first_name
            client.user.last_name = client.last_name
            linked_users.append(client.user)
            continue

        user = User.objects.using(database).create(
            username=_generated_username(client, used_usernames),
            password=make_password(None),
            first_name=client.first_name,
            last_name=client.last_name,
            is_client=True,
            is_active=False,
            must_change_password=True,
        )
        client.user_id = user.pk
        clients_to_link.append(client)

    if linked_users:
        User.objects.using(database).bulk_update(
            linked_users,
            ["first_name", "last_name"],
            batch_size=1000,
        )
    if clients_to_link:
        Client.objects.using(database).bulk_update(
            clients_to_link,
            ["user"],
            batch_size=1000,
        )


def restore_names_to_client(apps, schema_editor):
    Client = apps.get_model("clients", "Client")
    database = schema_editor.connection.alias

    clients = list(
        Client.objects.using(database)
        .select_related("user")
        .only(
            "id",
            "user_id",
            "first_name",
            "last_name",
            "user__first_name",
            "user__last_name",
        )
        .order_by("id")
    )
    missing_users = [client.pk for client in clients if client.user_id is None]
    if missing_users:
        raise RuntimeError(
            "Cannot restore Client names without linked users for clients: "
            + ", ".join(map(str, missing_users[:20]))
        )

    oversized = [
        client.pk
        for client in clients
        if len(client.user.first_name) > 100
        or len(client.user.last_name) > 100
    ]
    if oversized:
        raise RuntimeError(
            "Cannot restore Client names longer than 100 characters for clients: "
            + ", ".join(map(str, oversized[:20]))
        )

    for client in clients:
        client.first_name = client.user.first_name
        client.last_name = client.user.last_name
    if clients:
        Client.objects.using(database).bulk_update(
            clients,
            ["first_name", "last_name"],
            batch_size=1000,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_must_change_password"),
        ("clients", "0014_move_department_to_sales"),
    ]

    operations = [
        # New code writes names through User. Keep the legacy columns nullable
        # and physically present for a safe rollback during this release.
        migrations.AlterField(
            model_name="client",
            name="first_name",
            field=models.CharField(max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="client",
            name="last_name",
            field=models.CharField(
                blank=True,
                default="",
                max_length=100,
                null=True,
            ),
        ),
        migrations.RunPython(move_names_to_user, restore_names_to_client),
        migrations.AlterField(
            model_name="client",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="client_profile",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="client",
                    name="first_name",
                ),
                migrations.RemoveField(
                    model_name="client",
                    name="last_name",
                ),
            ],
        ),
    ]
