from apps.sys_permissions.tests.migration_case import PermissionMigrationTestCase

PAGE_CODES = {"dashboard.view", "tasks.own"}


class DashboardTasksPermissionsMigrationTests(PermissionMigrationTestCase):
    """Главная и «Задачи» были открыты всем: права на них получает каждый сотрудник."""

    rbac_from = "0028_bots_permissions"
    rbac_to = "0029_dashboard_tasks_permissions"

    def seed(self):
        self.cashier = self.employee("mig-page-cashier", "payments.create", "reports.view")
        self.newcomer = self.employee("mig-page-newcomer")

    def test_every_employee_keeps_both_pages(self):
        assert self.codes(self.cashier) == {"payments.create", "reports.view", *PAGE_CODES}
        assert self.codes(self.newcomer) == PAGE_CODES

    def test_catalog_rows_are_labelled(self):
        Permission = self.apps.get_model("rbac", "Permission")
        labels = dict(Permission.objects.filter(code__in=PAGE_CODES).values_list("code", "label"))
        assert labels == {"dashboard.view": "Главная: Доступ", "tasks.own": "Задачи: Доступ (свои задачи)"}
