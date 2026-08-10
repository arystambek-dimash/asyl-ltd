from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DepartmentAppMoveMigrationTests(TransactionTestCase):
    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("clients", "0013_client_last_name_optional"),
        ("employees", "0008_move_names_to_user"),
        ("sales", None),
    ]
    old_state_targets = [
        ("accounts", "0003_user_must_change_password"),
        ("clients", "0013_client_last_name_optional"),
        ("employees", "0008_move_names_to_user"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("sales", "0002_department_move_complete"),
        ("employees", "0010_department_move_complete"),
        ("clients", "0014_move_department_to_sales"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.old_state_targets).apps

        User = old_apps.get_model("accounts", "User")
        Employee = old_apps.get_model("employees", "Employee")
        Department = old_apps.get_model("clients", "Department")
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        Permission = old_apps.get_model("auth", "Permission")
        Group = old_apps.get_model("auth", "Group")

        User.objects.filter(username="department-app-move").delete()
        Department.objects.filter(code="department-app-move").delete()
        Group.objects.filter(name="department-app-move").delete()

        department = Department.objects.create(
            code="department-app-move",
            name="Безопасный перенос",
            color="#238C6E",
            is_active=True,
            is_default=False,
        )
        user = User.objects.create(username="department-app-move")
        employee = Employee.objects.create(
            user=user,
            sales_department=department,
        )

        content_type, _ = ContentType.objects.get_or_create(
            app_label="clients",
            model="department",
        )
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename="verify_department_app_move",
            defaults={"name": "Verify safe Department app move"},
        )
        group = Group.objects.create(name="department-app-move")
        group.permissions.add(permission)

        self.department_id = department.pk
        self.employee_id = employee.pk
        self.content_type_id = content_type.pk
        self.permission_id = permission.pk
        self.group_id = group.pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_rows_relations_table_and_permissions_keep_their_ids(self):
        Department = self.apps.get_model("sales", "Department")
        Employee = self.apps.get_model("employees", "Employee")
        ContentType = self.apps.get_model("contenttypes", "ContentType")
        Permission = self.apps.get_model("auth", "Permission")
        Group = self.apps.get_model("auth", "Group")

        with self.assertRaises(LookupError):
            self.apps.get_model("clients", "Department")

        department = Department.objects.get(pk=self.department_id)
        employee = Employee.objects.get(pk=self.employee_id)
        assert department.code == "department-app-move"
        assert department.name == "Безопасный перенос"
        assert department.color == "#238C6E"
        assert employee.sales_department_id == self.department_id
        assert employee.sales_department.pk == self.department_id

        tables = set(connection.introspection.table_names())
        assert "clients_department" in tables
        assert "sales_department" not in tables

        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, "employees_employee"
            )
        foreign_keys = {
            (details["columns"][0], details["foreign_key"][0])
            for details in constraints.values()
            if details.get("foreign_key") and len(details["columns"]) == 1
        }
        assert ("sales_department_id", "clients_department") in foreign_keys

        content_type = ContentType.objects.get(
            app_label="sales", model="department"
        )
        assert content_type.pk == self.content_type_id
        assert not ContentType.objects.filter(
            app_label="clients", model="department"
        ).exists()
        permission = Permission.objects.get(pk=self.permission_id)
        assert permission.content_type_id == self.content_type_id
        assert Group.objects.get(pk=self.group_id).permissions.filter(
            pk=self.permission_id
        ).exists()

        next_department = Department.objects.create(
            code="department-app-move-next",
            name="Проверка sequence",
        )
        assert next_department.pk > self.department_id

    def test_reverse_restores_historical_identity_without_data_loss(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.old_state_targets).apps

        Department = old_apps.get_model("clients", "Department")
        Employee = old_apps.get_model("employees", "Employee")
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        Permission = old_apps.get_model("auth", "Permission")

        with self.assertRaises(LookupError):
            old_apps.get_model("sales", "Department")

        department = Department.objects.get(pk=self.department_id)
        employee = Employee.objects.get(pk=self.employee_id)
        assert department.code == "department-app-move"
        assert employee.sales_department_id == self.department_id

        content_type = ContentType.objects.get(
            app_label="clients", model="department"
        )
        assert content_type.pk == self.content_type_id
        assert Permission.objects.get(pk=self.permission_id).content_type_id == (
            self.content_type_id
        )

    def test_app_scoped_migrate_completes_move_before_post_migrate(self):
        targets = {
            "sales": ("sales", "0002_department_move_complete"),
            "clients": ("clients", "0014_move_department_to_sales"),
            "employees": ("employees", "0010_department_move_complete"),
        }
        for app_label, target in targets.items():
            with self.subTest(app_label=app_label):
                executor = MigrationExecutor(connection)
                executor.migrate(self.migrate_from)

                call_command(
                    "migrate", app_label, interactive=False, verbosity=0
                )
                executor = MigrationExecutor(connection)
                completed_apps = executor.loader.project_state([target]).apps

                with self.assertRaises(LookupError):
                    completed_apps.get_model("clients", "Department")
                Department = completed_apps.get_model("sales", "Department")
                ContentType = completed_apps.get_model("contenttypes", "ContentType")

                assert Department._meta.db_table == "clients_department"
                assert Department.objects.get(pk=self.department_id).code == (
                    "department-app-move"
                )
                content_type = ContentType.objects.get(
                    app_label="sales", model="department"
                )
                assert content_type.pk == self.content_type_id
                assert not ContentType.objects.filter(
                    app_label="clients", model="department"
                ).exists()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
