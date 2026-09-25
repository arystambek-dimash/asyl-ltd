from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

ACCOUNTS = ("accounts", "0003_user_must_change_password")
EMPLOYEES = ("employees", "0011_grant_shipping_operators_payment_permissions")


class PermissionMigrationTestCase(TransactionTestCase):
    """Прогон одной миграции прав: сотрудники до неё, коды — после.

    Наследник задаёт ``rbac_from``/``rbac_to`` и в ``seed`` создаёт сотрудников
    через ``self.employee(username, *codes)`` на схеме до миграции.
    """

    rbac_from: str
    rbac_to: str

    def setUp(self):
        super().setUp()
        migrate_from = [ACCOUNTS, ("rbac", self.rbac_from), EMPLOYEES]
        migrate_to = [ACCOUNTS, ("rbac", self.rbac_to), EMPLOYEES]
        executor = MigrationExecutor(connection)
        executor.migrate(migrate_from)
        self._old_apps = executor.loader.project_state(migrate_from).apps
        self.seed()

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(migrate_to)
        self.apps = executor.loader.project_state(migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def seed(self):
        raise NotImplementedError

    def employee(self, username, *codes):
        User = self._old_apps.get_model("accounts", "User")
        Employee = self._old_apps.get_model("employees", "Employee")
        Permission = self._old_apps.get_model("rbac", "Permission")
        permissions = []
        for code in codes:
            section, action = code.split(".")
            permissions.append(Permission.objects.get_or_create(
                code=code, defaults={"section": section, "action": action, "label": code},
            )[0])
        emp = Employee.objects.create(user=User.objects.create(username=username), phone=username)
        emp.permissions.add(*permissions)
        return emp.pk

    def codes(self, pk):
        Employee = self.apps.get_model("employees", "Employee")
        return set(Employee.objects.get(pk=pk).permissions.values_list("code", flat=True))
