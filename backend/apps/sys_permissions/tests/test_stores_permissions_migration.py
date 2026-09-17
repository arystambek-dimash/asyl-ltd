from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class StoresPermissionsMigrationTests(TransactionTestCase):
    """Права «Магазины» выдаются тем, у кого было то же действие по клиентам."""

    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0025_simplify_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0026_stores_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Employee = old_apps.get_model("employees", "Employee")
        Permission = old_apps.get_model("rbac", "Permission")

        def perm(code):
            section, action = code.split(".")
            return Permission.objects.get_or_create(
                code=code, defaults={"section": section, "action": action, "label": code},
            )[0]

        def employee(username, *codes):
            user = User.objects.create(username=username)
            emp = Employee.objects.create(user=user, phone=username)
            emp.permissions.add(*[perm(code) for code in codes])
            return emp.pk

        self.viewer = employee("mig-store-viewer", "clients.view")
        self.editor = employee("mig-store-editor", "clients.view", "clients.create", "clients.edit", "clients.delete")
        self.cashier = employee("mig-store-cashier", "payments.create")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def codes(self, pk):
        Employee = self.apps.get_model("employees", "Employee")
        return set(Employee.objects.get(pk=pk).permissions.values_list("code", flat=True))

    def test_client_actions_become_store_actions(self):
        assert {"stores.view"} <= self.codes(self.viewer)
        assert not {"stores.create", "stores.edit", "stores.delete"} & self.codes(self.viewer)
        assert {"stores.view", "stores.create", "stores.edit", "stores.delete"} <= self.codes(self.editor)
        assert not any(code.startswith("stores.") for code in self.codes(self.cashier))

    def test_catalog_rows_are_labelled(self):
        Permission = self.apps.get_model("rbac", "Permission")
        labels = dict(Permission.objects.filter(section="stores").values_list("code", "label"))
        assert labels == {
            "stores.view": "Магазины: Просмотр",
            "stores.create": "Магазины: Создание",
            "stores.edit": "Магазины: Изменение",
            "stores.delete": "Магазины: Удаление",
        }
