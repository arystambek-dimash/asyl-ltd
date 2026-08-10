from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RoleRetirementMigrationTests(TransactionTestCase):
    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0017_silos_permission"),
        ("employees", "0006_employee_sales_department"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0018_retire_roles"),
        ("employees", "0007_flatten_role_permissions"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        User = old_apps.get_model("accounts", "User")
        Employee = old_apps.get_model("employees", "Employee")
        Department = old_apps.get_model("clients", "Department")
        Permission = old_apps.get_model("rbac", "Permission")
        Role = old_apps.get_model("rbac", "Role")

        # Archive tables intentionally survive Django's normal test flush.
        # Remove only this test's prior rows when --keepdb is used.
        User.objects.filter(username="migration-employee").delete()
        Role.objects.filter(name="Старший менеджер").delete()
        Department.objects.filter(code="migration-sales").delete()
        Employee.denied_permissions.through.objects.all().delete()

        def permission(code):
            row, _ = Permission.objects.get_or_create(
                code=code,
                defaults={
                    "section": code.split(".")[0],
                    "action": code.split(".")[1],
                    "label": code,
                },
            )
            return row

        direct = permission("warehouse.view")
        renamed = permission("rbac.manage")
        inherited = permission("orders.view")
        denied = permission("orders.edit")

        role = Role.objects.create(name="Старший менеджер", is_system=False)
        role.permissions.add(inherited, denied)
        department = Department.objects.create(
            code="migration-sales", name="Миграционный отдел", color="#315FD5"
        )
        user = User.objects.create(username="migration-employee")
        employee = Employee.objects.create(
            user=user,
            first_name="Миграция",
            last_name="Прав",
            position="",
            role=role,
            sales_department=department,
        )
        employee.permissions.add(direct, renamed)
        employee.denied_permissions.add(denied)

        self.employee_id = employee.pk
        self.renamed_permission_id = renamed.pk
        self.existing_permission_ids = set(
            Permission.objects.values_list("id", flat=True)
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_effective_access_and_labels_are_materialized_without_data_loss(self):
        Employee = self.apps.get_model("employees", "Employee")
        Permission = self.apps.get_model("rbac", "Permission")
        employee = Employee.objects.get(pk=self.employee_id)

        codes = set(employee.permissions.values_list("code", flat=True))
        assert {
            "warehouse.view",
            "sys_permissions.manage",
            "orders.view",
            "orders.create",
            "clients.view",
            "catalog.view",
        } <= codes
        assert "orders.edit" not in codes
        assert employee.position == "Старший менеджер"
        assert Permission.objects.get(code="sys_permissions.manage").pk == (
            self.renamed_permission_id
        )
        assert self.existing_permission_ids <= set(
            Permission.objects.values_list("id", flat=True)
        )

    def test_role_is_removed_from_state_but_legacy_tables_remain(self):
        Employee = self.apps.get_model("employees", "Employee")
        with self.assertRaises(FieldDoesNotExist):
            Employee._meta.get_field("role")
        with self.assertRaises(LookupError):
            self.apps.get_model("rbac", "Role")

        tables = set(connection.introspection.table_names())
        assert "rbac_role" not in tables
        assert "rbac_role_permissions" not in tables
        assert "employees_employee_denied_permissions" not in tables
        assert "legacy_rbac_role" in tables
        assert "legacy_rbac_role_permissions" in tables
        assert "legacy_employees_employee_denied_permissions" in tables

    def test_reverse_restores_role_data_and_foreign_keys(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        Employee = old_apps.get_model("employees", "Employee")
        employee = Employee.objects.get(pk=self.employee_id)
        assert employee.role.name == "Старший менеджер"
        assert set(employee.role.permissions.values_list("code", flat=True)) == {
            "orders.edit",
            "orders.view",
        }
        assert set(employee.denied_permissions.values_list("code", flat=True)) == {
            "orders.edit"
        }

        expected_foreign_keys = {
            "rbac_role_permissions": {
                ("permission_id", "rbac_permission"),
                ("role_id", "rbac_role"),
            },
            "employees_employee_denied_permissions": {
                ("employee_id", "employees_employee"),
                ("permission_id", "rbac_permission"),
            },
        }
        with connection.cursor() as cursor:
            for table, expected in expected_foreign_keys.items():
                constraints = connection.introspection.get_constraints(
                    cursor, table
                )
                actual = {
                    (details["columns"][0], details["foreign_key"][0])
                    for details in constraints.values()
                    if details.get("foreign_key")
                }
                assert actual == expected

    def test_reverse_keeps_orphaned_archive_rows_with_not_valid_foreign_keys(self):
        Employee = self.apps.get_model("employees", "Employee")
        Permission = self.apps.get_model("rbac", "Permission")
        Employee.objects.filter(pk=self.employee_id).delete()
        Permission.objects.filter(code="orders.edit").delete()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        constraint_names = {
            "rbac_role_perm_permission_restore_fk",
            "employee_denied_employee_restore_fk",
            "employee_denied_permission_restore_fk",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname, convalidated FROM pg_constraint "
                "WHERE conname IN (%s, %s, %s)",
                tuple(sorted(constraint_names)),
            )
            constraints = dict(cursor.fetchall())

        assert set(constraints) == constraint_names
        assert not any(constraints.values())
