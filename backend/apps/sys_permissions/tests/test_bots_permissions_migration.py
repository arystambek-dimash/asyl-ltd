from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class BotPermissionsMigrationTests(TransactionTestCase):
    """Журнал WhatsApp-бота сразу видят администраторы; у остальных права не меняются."""

    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0027_loader_transport_permissions"),
        ("employees", "0011_grant_shipping_operators_payment_permissions"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("rbac", "0028_bots_permissions"),
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

        self.admin = employee("mig-bot-admin", "sys_permissions.manage", "events.view")
        self.loader = employee("mig-bot-loader", "loader.view", "loader.wagons")

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

    def test_admins_get_the_journal_and_others_keep_their_rights(self):
        assert self.codes(self.admin) == {"sys_permissions.manage", "events.view", "bots.view", "bots.manage"}
        assert self.codes(self.loader) == {"loader.view", "loader.wagons"}

    def test_catalog_rows_are_labelled(self):
        Permission = self.apps.get_model("rbac", "Permission")
        labels = dict(Permission.objects.filter(section="bots").values_list("code", "label"))
        assert labels == {"bots.view": "WhatsApp-бот: Журнал сообщений",
                          "bots.manage": "WhatsApp-бот: Провести и пропустить сообщение"}
