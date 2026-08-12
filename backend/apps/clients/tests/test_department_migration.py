from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase


MIGRATE_FROM = [("clients", "0015_move_names_to_user")]
MIGRATE_TO = [("clients", "0016_client_department")]


class ClientDepartmentMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        old_apps = executor.loader.project_state(MIGRATE_FROM).apps

        User = old_apps.get_model("accounts", "User")
        Client = old_apps.get_model("clients", "Client")
        user = User.objects.create(
            username="client-before-department",
            first_name="Старый",
            is_client=True,
        )
        client = Client.objects.create(user=user, phone="+77000000000")
        self.client_id = client.pk
        self.user_id = user.pk

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_TO)
        self.apps = executor.loader.project_state(MIGRATE_TO).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_client_is_preserved_without_inventing_a_department(self):
        Client = self.apps.get_model("clients", "Client")
        client = Client.objects.get(pk=self.client_id)

        assert client.user_id == self.user_id
        assert client.phone == "+77000000000"
        assert client.department_id is None

    def test_relation_is_optional_and_protects_department_ownership(self):
        Client = self.apps.get_model("clients", "Client")
        Department = self.apps.get_model("sales", "Department")
        department = Department.objects.create(
            code="migration-client-team",
            name="Миграционный отдел",
        )
        client = Client.objects.get(pk=self.client_id)
        client.department = department
        client.save(update_fields=["department"])

        with self.assertRaises(ProtectedError):
            department.delete()

        client.refresh_from_db()
        assert client.department_id == department.pk
        field = Client._meta.get_field("department")
        assert field.null is True
        assert field.remote_field.on_delete is models.PROTECT
        assert field.remote_field.related_name == "clients"
