from django.contrib.auth.hashers import is_password_usable
from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.db.models.deletion import PROTECT, ProtectedError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils.text import slugify


MIGRATE_FROM = [
    ("accounts", "0003_user_must_change_password"),
    ("clients", "0014_move_department_to_sales"),
]
MIGRATE_TO = [
    ("accounts", "0003_user_must_change_password"),
    ("clients", "0015_move_names_to_user"),
]


class ClientNameMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        old_apps = executor.loader.project_state(MIGRATE_FROM).apps

        User = old_apps.get_model("accounts", "User")
        Client = old_apps.get_model("clients", "Client")

        linked_user = User.objects.create(
            username="existing-client-account",
            password="existing-password-hash",
            first_name="",
            last_name="",
            is_client=True,
        )
        linked_client = Client.objects.create(
            user=linked_user,
            first_name="Алия",
            last_name="Серикова",
            phone="1",
        )
        generated_client = Client.objects.create(
            first_name="Айжан",
            last_name="",
            phone="2",
        )
        duplicate_name_client = Client.objects.create(
            first_name="Айжан",
            last_name="",
            phone="3",
        )
        colliding_username = (
            f"{slugify(generated_client.first_name, allow_unicode=True)}"
            f"-{generated_client.pk}"
        )
        User.objects.create(
            username=colliding_username,
            password="unrelated-password-hash",
        )

        self.linked_user_id = linked_user.pk
        self.linked_client_id = linked_client.pk
        self.generated_client_id = generated_client.pk
        self.duplicate_name_client_id = duplicate_name_client.pk
        self.colliding_username = colliding_username

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_TO)
        self.apps = executor.loader.project_state(MIGRATE_TO).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_names_and_existing_credentials_are_preserved(self):
        User = self.apps.get_model("accounts", "User")
        Client = self.apps.get_model("clients", "Client")

        user = User.objects.get(pk=self.linked_user_id)
        client = Client.objects.get(pk=self.linked_client_id)

        assert client.user_id == user.pk
        assert user.username == "existing-client-account"
        assert user.password == "existing-password-hash"
        assert user.first_name == "Алия"
        assert user.last_name == "Серикова"
        assert user.is_active is True
        assert user.must_change_password is False

    def test_unlinked_clients_receive_safe_unique_accounts(self):
        Client = self.apps.get_model("clients", "Client")
        first = Client.objects.select_related("user").get(
            pk=self.generated_client_id
        )
        second = Client.objects.select_related("user").get(
            pk=self.duplicate_name_client_id
        )

        assert first.user.username == f"{self.colliding_username}-2"
        assert second.user.username == f"айжан-{second.pk}"
        assert first.user.username != second.user.username
        for client in (first, second):
            assert client.user.first_name == "Айжан"
            assert client.user.last_name == ""
            assert client.user.is_client is True
            assert client.user.is_active is False
            assert client.user.must_change_password is True
            assert not is_password_usable(client.user.password)

    def test_fields_leave_state_but_remain_as_rollback_columns(self):
        Client = self.apps.get_model("clients", "Client")

        with self.assertRaises(FieldDoesNotExist):
            Client._meta.get_field("first_name")
        with self.assertRaises(FieldDoesNotExist):
            Client._meta.get_field("last_name")

        user_field = Client._meta.get_field("user")
        assert user_field.null is False
        assert user_field.remote_field.on_delete is PROTECT

        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, "clients_client"
                )
            }
        assert {"first_name", "last_name"} <= columns

    def test_linked_user_is_protected_from_deletion(self):
        User = self.apps.get_model("accounts", "User")

        with self.assertRaises(ProtectedError):
            User.objects.get(pk=self.linked_user_id).delete()

    def test_reverse_restores_current_user_names_and_keeps_accounts(self):
        User = self.apps.get_model("accounts", "User")
        generated_user_id = self.apps.get_model("clients", "Client").objects.get(
            pk=self.generated_client_id
        ).user_id
        User.objects.filter(pk=self.linked_user_id).update(
            first_name="Обновлённое",
            last_name="Имя",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        old_apps = executor.loader.project_state(MIGRATE_FROM).apps
        Client = old_apps.get_model("clients", "Client")
        OldUser = old_apps.get_model("accounts", "User")

        linked_client = Client.objects.get(pk=self.linked_client_id)
        generated_client = Client.objects.get(pk=self.generated_client_id)
        assert linked_client.first_name == "Обновлённое"
        assert linked_client.last_name == "Имя"
        assert linked_client.user_id == self.linked_user_id
        assert generated_client.user_id == generated_user_id
        assert OldUser.objects.filter(pk=generated_user_id).exists()

    def test_reverse_rejects_names_too_long_for_legacy_columns(self):
        User = self.apps.get_model("accounts", "User")
        User.objects.filter(pk=self.linked_user_id).update(first_name="x" * 101)

        executor = MigrationExecutor(connection)
        with self.assertRaisesRegex(RuntimeError, "longer than 100"):
            executor.migrate(MIGRATE_FROM)

        User.objects.filter(pk=self.linked_user_id).update(first_name="Алия")


class _FailedClientNameMigrationTest(TransactionTestCase):
    user_updates = {}

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        self.old_apps = executor.loader.project_state(MIGRATE_FROM).apps

        User = self.old_apps.get_model("accounts", "User")
        Client = self.old_apps.get_model("clients", "Client")
        safe_client = Client.objects.create(
            first_name="Не менять",
            last_name="При ошибке",
            phone="1",
        )
        user_values = {
            "is_client": True,
            **self.user_updates,
        }
        bad_user = User.objects.create(
            username=self.__class__.__name__.lower(),
            password="existing-password-hash",
            **user_values,
        )
        Client.objects.create(
            user=bad_user,
            first_name="Имя клиента",
            last_name="Фамилия",
            phone="2",
        )
        self.safe_client_id = safe_client.pk
        self.bad_user_id = bad_user.pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        User = executor.loader.project_state(MIGRATE_FROM).apps.get_model(
            "accounts", "User"
        )
        User.objects.filter(pk=self.bad_user_id).update(
            first_name="",
            last_name="",
            is_client=True,
            is_staff=False,
            is_superuser=False,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _assert_migration_fails_atomically(self, message):
        User = self.old_apps.get_model("accounts", "User")
        before_user_ids = set(User.objects.values_list("id", flat=True))

        executor = MigrationExecutor(connection)
        with self.assertRaisesRegex(RuntimeError, message):
            executor.migrate(MIGRATE_TO)

        assert set(User.objects.values_list("id", flat=True)) == before_user_ids
        assert self.old_apps.get_model("clients", "Client").objects.get(
            pk=self.safe_client_id
        ).user_id is None


class ClientNameConflictMigrationTests(_FailedClientNameMigrationTest):
    user_updates = {"first_name": "Другое имя"}

    def test_name_conflict_aborts_without_partial_writes(self):
        self._assert_migration_fails_atomically("name conflicts detected")


class ClientRoleConflictMigrationTests(_FailedClientNameMigrationTest):
    user_updates = {"is_client": False}

    def test_incompatible_account_role_aborts_without_partial_writes(self):
        self._assert_migration_fails_atomically("incompatible role flags")
