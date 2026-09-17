from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SimplifyPermissionsMigrationTests(TransactionTestCase):
    """Упрощение каталога: никто не теряет доступ, старые коды уходят, подписи обновлены."""

    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0024_orders_confirm_all_permission"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0025_simplify_permissions"),
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

        self.shipper = employee("mig-shipper", "shipping.ship", "orders.view")
        self.watcher = employee("mig-watcher", "shipping.view")
        self.wagons = employee("mig-wagons", "train.load")
        self.ai = employee("mig-ai", "ai_247.manage")
        self.rollback = employee("mig-rollback", "shipping.rollback")
        self.dead = employee("mig-dead", "catalog.delete", "sys_permissions.view", "grain.lab", "catalog.edit")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        # Вернуть схему на последнюю миграцию для остальных тестов.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def codes(self, pk):
        Employee = self.apps.get_model("employees", "Employee")
        return set(Employee.objects.get(pk=pk).permissions.values_list("code", flat=True))

    def test_nobody_loses_access_and_retired_codes_are_gone(self):
        assert self.codes(self.shipper) == {"monoblock.view", "loader.view", "loader.confirm", "orders.view"}
        assert self.codes(self.watcher) == {"monoblock.view"}
        assert self.codes(self.wagons) == {"monoblock.view", "loader.view", "loader.confirm"}
        assert self.codes(self.ai) == {"monoblock.view"}
        assert self.codes(self.rollback) == {"orders.rollback"}
        assert self.codes(self.dead) == {"catalog.edit"}
        Permission = self.apps.get_model("rbac", "Permission")
        assert not Permission.objects.filter(code__startswith="shipping.").exists()
        assert not Permission.objects.filter(code__in=["train.load", "ai_247.manage", "grain.lab"]).exists()
        assert (
            Permission.objects.get(code="grain.weigh").label
            == "Приход и вывоз: Взвешивание и остановки под аркой"
        )
