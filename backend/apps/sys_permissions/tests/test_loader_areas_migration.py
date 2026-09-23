from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LoaderAreasMigrationTests(TransactionTestCase):
    """Области «Фуры» и «Вагоны» получает каждый, кто работал на странице грузчика."""

    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0026_stores_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0027_loader_transport_permissions"),
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

        self.loader = employee("mig-loader", "loader.view", "loader.confirm", "monoblock.view")
        self.viewer = employee("mig-loader-viewer", "loader.view")
        self.shipper = employee("mig-loader-shipper", "loader.confirm")
        self.cashier = employee("mig-loader-cashier", "payments.create")

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

    def test_loader_holders_get_both_areas_and_keep_their_rights(self):
        areas = {"loader.trucks", "loader.wagons"}
        assert self.codes(self.loader) == {"loader.view", "loader.confirm", "monoblock.view", *areas}
        assert self.codes(self.viewer) == {"loader.view", *areas}
        assert self.codes(self.shipper) == {"loader.confirm", *areas}
        assert self.codes(self.cashier) == {"payments.create"}

    def test_catalog_rows_are_labelled(self):
        Permission = self.apps.get_model("rbac", "Permission")
        labels = dict(
            Permission.objects.filter(code__in=("loader.trucks", "loader.wagons")).values_list("code", "label"))
        assert labels == {"loader.trucks": "Грузчик: Фуры", "loader.wagons": "Грузчик: Вагоны"}
