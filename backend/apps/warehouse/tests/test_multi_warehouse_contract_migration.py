from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MultiWarehouseContractMigrationTests(TransactionTestCase):
    migrate_from = [("warehouse", "0009_multi_warehouse_expand")]
    migrate_to = [("warehouse", "0010_multi_warehouse_stock_transfer")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Product = old_apps.get_model("catalog", "Product")
        Warehouse = old_apps.get_model("warehouse", "Warehouse")
        StockItem = old_apps.get_model("warehouse", "StockItem")

        Warehouse.objects.filter(is_default=True).update(is_default=False)
        self.main, _created = Warehouse.objects.update_or_create(
            code="main",
            defaults={
                "name": "Основной склад",
                "address": "",
                "is_active": True,
                "is_default": True,
            },
        )
        self.secondary = Warehouse.objects.create(
            code="second",
            name="Мельница 2",
        )
        self.product = Product.objects.create(
            name="Миграционный Phase 2",
            color="Red",
            weight_kg="50",
        )
        StockItem.objects.create(
            product=self.product,
            warehouse=self.main,
            bags=10,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_product_can_have_one_stock_row_per_warehouse(self):
        StockItem = self.apps.get_model("warehouse", "StockItem")

        StockItem.objects.create(
            product_id=self.product.pk,
            warehouse_id=self.secondary.pk,
            bags=4,
        )

        assert set(
            StockItem.objects.filter(product_id=self.product.pk).values_list(
                "warehouse_id", "bags"
            )
        ) == {(self.main.pk, 10), (self.secondary.pk, 4)}
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockItem.objects.create(
                product_id=self.product.pk,
                warehouse_id=self.secondary.pk,
                bags=1,
            )

    def test_constraints_drop_global_unique_and_keep_null_and_name_guards(self):
        with connection.cursor() as cursor:
            stock_constraints = connection.introspection.get_constraints(
                cursor,
                "warehouse_stockitem",
            )
            warehouse_constraints = connection.introspection.get_constraints(
                cursor,
                "warehouse_warehouse",
            )

        assert "wh_stock_product_uniq_compat" not in stock_constraints
        assert stock_constraints["wh_stock_wh_product_uniq"]["unique"] is True
        assert stock_constraints["wh_stock_null_product_uniq"]["unique"] is True
        assert warehouse_constraints["warehouse_name_ci_uniq"]["unique"] is True

    def test_legacy_implicit_event_fails_closed_for_ambiguous_product(self):
        if connection.vendor != "postgresql":
            self.skipTest("legacy write guards are PostgreSQL functions")
        StockItem = self.apps.get_model("warehouse", "StockItem")
        StockMovement = self.apps.get_model("warehouse", "StockMovement")
        StockItem.objects.create(
            product_id=self.product.pk,
            warehouse_id=self.secondary.pk,
            bags=4,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            StockMovement.objects.create(
                product_id=self.product.pk,
                warehouse_id=None,
                delta=1,
                balance_after=11,
                reason="adjustment",
            )

        movement = StockMovement.objects.create(
            product_id=self.product.pk,
            warehouse_id=self.secondary.pk,
            delta=1,
            balance_after=5,
            reason="adjustment",
        )
        assert movement.warehouse_id == self.secondary.pk
