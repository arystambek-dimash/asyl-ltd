from apps.sys_permissions.tests.migration_case import PermissionMigrationTestCase


class LoaderAreasMigrationTests(PermissionMigrationTestCase):
    """Области «Фуры» и «Вагоны» получает каждый, кто работал на странице грузчика."""

    rbac_from = "0026_stores_permissions"
    rbac_to = "0027_loader_transport_permissions"

    def seed(self):
        self.loader = self.employee("mig-loader", "loader.view", "loader.confirm", "monoblock.view")
        self.viewer = self.employee("mig-loader-viewer", "loader.view")
        self.shipper = self.employee("mig-loader-shipper", "loader.confirm")
        self.cashier = self.employee("mig-loader-cashier", "payments.create")

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
