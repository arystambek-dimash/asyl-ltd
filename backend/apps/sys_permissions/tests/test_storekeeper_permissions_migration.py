from apps.sys_permissions.tests.migration_case import PermissionMigrationTestCase

STOREKEEPER_CODES = {"storekeeper.view", "storekeeper.confirm"}


class StorekeeperPermissionsMigrationTests(PermissionMigrationTestCase):
    """Страница «Кладовщик» новая: права появляются в каталоге, сами никому не выдаются."""

    rbac_from = "0030_bots_telegram_label"
    rbac_to = "0031_storekeeper_permissions"

    def seed(self):
        self.warehouse_keeper = self.employee("mig-storekeeper", "warehouse.view", "warehouse.adjust")

    def test_codes_are_in_the_catalog_and_nobody_gets_them_silently(self):
        Permission = self.apps.get_model("rbac", "Permission")
        labels = dict(Permission.objects.filter(code__in=STOREKEEPER_CODES).values_list("code", "label"))

        assert labels == {
            "storekeeper.view": "Кладовщик: Возвраты на приёмку",
            "storekeeper.confirm": "Кладовщик: Приёмка и закрытие возврата",
        }
        assert self.codes(self.warehouse_keeper) == {"warehouse.view", "warehouse.adjust"}
