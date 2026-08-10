from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class EmployeeNameMigrationTests(TransactionTestCase):
    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0018_retire_roles"),
        ("employees", "0007_flatten_role_permissions"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0018_retire_roles"),
        ("employees", "0008_move_names_to_user"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        User = old_apps.get_model("accounts", "User")
        Employee = old_apps.get_model("employees", "Employee")
        User.objects.filter(username="employee-name-migration").delete()

        user = User.objects.create(
            username="employee-name-migration",
        )
        employee = Employee.objects.create(
            user=user,
            first_name="Алия",
            last_name="Серикова",
        )
        partial_user = User.objects.create(
            username="employee-name-migration-partial",
            first_name="Совпадает",
        )
        Employee.objects.create(
            user=partial_user,
            first_name="Совпадает",
            last_name="Заполнится",
        )
        self.user_id = user.pk
        self.employee_id = employee.pk
        self.partial_user_id = partial_user.pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_employee_name_is_preserved_on_user(self):
        User = self.apps.get_model("accounts", "User")
        user = User.objects.get(pk=self.user_id)

        assert user.first_name == "Алия"
        assert user.last_name == "Серикова"

        partial_user = User.objects.get(pk=self.partial_user_id)
        assert partial_user.first_name == "Совпадает"
        assert partial_user.last_name == "Заполнится"

    def test_employee_name_fields_are_removed(self):
        Employee = self.apps.get_model("employees", "Employee")

        with self.assertRaises(FieldDoesNotExist):
            Employee._meta.get_field("first_name")
        with self.assertRaises(FieldDoesNotExist):
            Employee._meta.get_field("last_name")

        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, "employees_employee"
                )
            }
        assert {"first_name", "last_name"} <= columns

    def test_reverse_restores_employee_names_from_user(self):
        User = self.apps.get_model("accounts", "User")
        User.objects.filter(pk=self.user_id).update(
            first_name="Обновлённое",
            last_name="Имя",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Employee = old_apps.get_model("employees", "Employee")
        employee = Employee.objects.get(pk=self.employee_id)

        assert employee.first_name == "Обновлённое"
        assert employee.last_name == "Имя"


class EmployeeNameConflictMigrationTests(TransactionTestCase):
    migrate_from = EmployeeNameMigrationTests.migrate_from
    migrate_to = EmployeeNameMigrationTests.migrate_to

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

        User = self.old_apps.get_model("accounts", "User")
        Employee = self.old_apps.get_model("employees", "Employee")
        User.objects.filter(username__startswith="employee-name-conflict-").delete()

        safe_user = User.objects.create(username="employee-name-conflict-safe")
        Employee.objects.create(
            user=safe_user,
            first_name="Не менять при ошибке",
            last_name="Тест",
        )
        conflict_user = User.objects.create(
            username="employee-name-conflict-existing",
            first_name="Другое имя",
        )
        Employee.objects.create(
            user=conflict_user,
            first_name="Имя сотрудника",
            last_name="Тест",
        )
        self.safe_user_id = safe_user.pk
        self.conflict_user_id = conflict_user.pk

    def tearDown(self):
        # Resolve the deliberately-created conflict so the database can return
        # to the current migration leaf even when the assertion fails.
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        old_apps.get_model("accounts", "User").objects.filter(
            pk=self.conflict_user_id
        ).update(first_name="")
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_conflict_aborts_without_partial_name_updates(self):
        executor = MigrationExecutor(connection)
        with self.assertRaisesRegex(RuntimeError, "name conflicts detected"):
            executor.migrate(self.migrate_to)

        User = self.old_apps.get_model("accounts", "User")
        safe_user = User.objects.get(pk=self.safe_user_id)
        conflict_user = User.objects.get(pk=self.conflict_user_id)
        assert safe_user.first_name == ""
        assert safe_user.last_name == ""
        assert conflict_user.first_name == "Другое имя"
