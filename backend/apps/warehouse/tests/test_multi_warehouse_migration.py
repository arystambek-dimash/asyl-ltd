from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MultiWarehouseExpandMigrationTests(TransactionTestCase):
    migrate_from = [("warehouse", "0008_alter_stockmovement_reason")]
    migrate_to = [("warehouse", "0009_multi_warehouse_expand")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self.old_apps = old_apps

        Product = old_apps.get_model("catalog", "Product")
        StockItem = old_apps.get_model("warehouse", "StockItem")
        StockReceipt = old_apps.get_model("warehouse", "StockReceipt")
        StockMovement = old_apps.get_model("warehouse", "StockMovement")

        product = Product.objects.create(
            name="Миграционный товар",
            color="Red",
            weight_kg="50",
        )
        stock = StockItem.objects.create(product=product, bags=41)
        receipt = StockReceipt.objects.create(product=product, bags=41)
        movement = StockMovement.objects.create(
            product=product,
            delta=41,
            balance_after=41,
            reason="receipt",
        )
        self.product_id = product.pk
        self.stock_id = stock.pk
        self.receipt_id = receipt.pk
        self.movement_id = movement.pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_default_warehouse_is_deterministic_and_legacy_rows_are_backfilled(self):
        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        StockItem = self.apps.get_model("warehouse", "StockItem")
        StockReceipt = self.apps.get_model("warehouse", "StockReceipt")
        StockMovement = self.apps.get_model("warehouse", "StockMovement")

        warehouse = Warehouse.objects.get(code="main")
        assert warehouse.name == "Основной склад"
        assert warehouse.is_active is True
        assert warehouse.is_default is True
        assert StockItem.objects.get(pk=self.stock_id).warehouse_id == warehouse.pk
        assert StockReceipt.objects.get(pk=self.receipt_id).warehouse_id == warehouse.pk
        assert (
            StockMovement.objects.get(pk=self.movement_id).warehouse_id == warehouse.pk
        )

    def test_rollback_image_can_insert_null_but_global_product_unique_remains(self):
        OldProduct = self.old_apps.get_model("catalog", "Product")
        OldStockItem = self.old_apps.get_model("warehouse", "StockItem")
        StockItem = self.apps.get_model("warehouse", "StockItem")
        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        product = OldProduct.objects.create(
            name="После expand",
            color="Blue",
            weight_kg="25",
        )

        row = OldStockItem.objects.create(product=product, bags=2)

        stored = StockItem.objects.get(pk=row.pk)
        assert stored.warehouse_id == Warehouse.objects.get(code="main").pk
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockItem.objects.create(product_id=product.pk, bags=3)

    def test_rollback_receipts_and_movements_follow_product_ownership(self):
        if connection.vendor != "postgresql":
            self.skipTest("legacy write guards are PostgreSQL triggers")

        OldProduct = self.old_apps.get_model("catalog", "Product")
        OldStockReceipt = self.old_apps.get_model("warehouse", "StockReceipt")
        OldStockMovement = self.old_apps.get_model("warehouse", "StockMovement")
        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        StockItem = self.apps.get_model("warehouse", "StockItem")
        StockReceipt = self.apps.get_model("warehouse", "StockReceipt")
        StockMovement = self.apps.get_model("warehouse", "StockMovement")

        secondary = Warehouse.objects.create(code="north", name="Северный склад")
        product = OldProduct.objects.create(
            name="Товар северного склада",
            color="Green",
            weight_kg="50",
        )
        StockItem.objects.create(
            product_id=product.pk,
            warehouse_id=secondary.pk,
            bags=7,
        )

        receipt = OldStockReceipt.objects.create(product_id=product.pk, bags=7)
        movement = OldStockMovement.objects.create(
            product_id=product.pk,
            delta=7,
            balance_after=7,
            reason="receipt",
        )

        assert StockReceipt.objects.get(pk=receipt.pk).warehouse_id == secondary.pk
        assert StockMovement.objects.get(pk=movement.pk).warehouse_id == secondary.pk

    def test_cross_warehouse_events_and_assignment_deletes_are_rejected(self):
        if connection.vendor != "postgresql":
            self.skipTest("warehouse invariants are PostgreSQL triggers")

        Product = self.apps.get_model("catalog", "Product")
        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        StockItem = self.apps.get_model("warehouse", "StockItem")
        StockReceipt = self.apps.get_model("warehouse", "StockReceipt")
        main = Warehouse.objects.get(code="main")
        secondary = Warehouse.objects.create(code="south", name="Южный склад")
        product = Product.objects.create(
            name="Товар южного склада",
            color="Red",
            weight_kg="50",
        )
        stock = StockItem.objects.create(
            product=product,
            warehouse=secondary,
            bags=4,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            StockReceipt.objects.create(
                product=product,
                warehouse=main,
                bags=1,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            stock.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockItem.objects.get(pk=self.stock_id).delete()

    def test_product_cascade_can_remove_its_stock_assignment(self):
        if connection.vendor != "postgresql":
            self.skipTest("warehouse invariants are PostgreSQL triggers")

        Product = self.apps.get_model("catalog", "Product")
        StockItem = self.apps.get_model("warehouse", "StockItem")
        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        product = Product.objects.create(
            name="Удаляемый товар",
            color="Blue",
            weight_kg="25",
        )
        stock = StockItem.objects.create(
            product=product,
            warehouse=Warehouse.objects.get(code="main"),
            bags=0,
        )

        # Historical migration models do not carry current model methods, so
        # set the same exact transaction-local marker as Product.delete().
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config(%s, %s, TRUE)",
                    ["asyl.deleting_product_id", str(product.pk)],
                )
            product.delete()

        assert not StockItem.objects.filter(pk=stock.pk).exists()

    def test_main_compatibility_anchor_cannot_change_or_be_disabled(self):
        if connection.vendor != "postgresql":
            self.skipTest("warehouse invariants are PostgreSQL triggers")

        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        main = Warehouse.objects.get(code="main")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Warehouse.objects.filter(pk=main.pk).update(code="renamed")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Warehouse.objects.filter(pk=main.pk).update(is_active=False)

        secondary = Warehouse.objects.create(code="default-2", name="Новый основной")
        Warehouse.objects.filter(pk=main.pk).update(is_default=False)
        Warehouse.objects.filter(pk=secondary.pk).update(is_default=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            secondary.delete()

    def test_named_compatibility_and_per_warehouse_constraints_exist(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                "warehouse_stockitem",
            )

        assert constraints["wh_stock_product_uniq_compat"]["unique"] is True
        assert constraints["wh_stock_wh_product_uniq"]["unique"] is True
