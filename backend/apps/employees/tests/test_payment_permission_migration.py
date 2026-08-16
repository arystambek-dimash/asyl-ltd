from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ShippingOperatorPaymentPermissionMigrationTests(TransactionTestCase):
    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0020_client_manage_access_permission"),
        ("employees", "0010_department_move_complete"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0020_client_manage_access_permission"),
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

        # ``TransactionTestCase`` flushes data but not migration history.  A
        # migration test may therefore run after another such test with the
        # catalog rows gone; seed the exact pre-migration contract explicitly.
        def permission(code, section, action, label):
            return Permission.objects.get_or_create(
                code=code,
                defaults={
                    "section": section,
                    "action": action,
                    "label": label,
                },
            )[0]

        shipping_load = permission(
            "shipping.load", "shipping", "load", "Пост отгрузки: Загрузка",
        )
        payments_view = permission(
            "payments.view", "payments", "view", "Оплаты: Просмотр",
        )
        permission(
            "payments.create", "payments", "create", "Оплаты: Создание",
        )

        eligible_user = User.objects.create(username="migration-shipping-payment")
        eligible = Employee.objects.create(
            user=eligible_user,
            # The title is deliberately unrelated: capability, not position,
            # is the migration's authorization signal.
            position="Сотрудник поста",
        )
        eligible.permissions.add(shipping_load)

        titled_user = User.objects.create(username="migration-title-only-operator")
        title_only = Employee.objects.create(
            user=titled_user,
            position="Оператор",
        )
        title_only.permissions.add(payments_view)

        self.eligible_id = eligible.pk
        self.title_only_id = title_only.pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_grant_uses_shipping_capability_and_never_position(self):
        Employee = self.apps.get_model("employees", "Employee")

        eligible = Employee.objects.get(pk=self.eligible_id)
        codes = set(eligible.permissions.values_list("code", flat=True))

        assert {"shipping.load", "payments.view", "payments.create"} <= codes

        title_only = Employee.objects.get(pk=self.title_only_id)
        codes = set(title_only.permissions.values_list("code", flat=True))

        assert codes == {"payments.view"}
