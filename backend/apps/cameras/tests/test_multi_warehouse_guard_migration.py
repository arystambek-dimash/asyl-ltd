from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

MIGRATE_FROM = [
    ("accounts", "0003_user_must_change_password"),
    ("cameras", "0029_ai247_warehouse_route"),
    ("orders", "0034_multi_warehouse_stock_guard"),
    ("warehouse", "0010_multi_warehouse_stock_transfer"),
]
MIGRATE_TO = [
    ("accounts", "0003_user_must_change_password"),
    ("cameras", "0030_multi_warehouse_stock_guard"),
    ("orders", "0034_multi_warehouse_stock_guard"),
    ("warehouse", "0010_multi_warehouse_stock_transfer"),
]


class MultiWarehouseCameraGuardMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        apps = executor.loader.project_state(MIGRATE_FROM).apps
        Batch = apps.get_model("cameras", "AlwaysOnStockBatch")
        Product = apps.get_model("catalog", "Product")
        StockItem = apps.get_model("warehouse", "StockItem")
        StockReceipt = apps.get_model("warehouse", "StockReceipt")
        Warehouse = apps.get_model("warehouse", "Warehouse")

        Warehouse.objects.filter(is_default=True).update(is_default=False)
        main, _created = Warehouse.objects.update_or_create(
            code="main",
            defaults={
                "name": "Основной склад",
                "address": "",
                "is_active": True,
                "is_default": True,
            },
        )
        secondary = Warehouse.objects.create(code="second", name="Мельница 2")
        product = Product.objects.create(
            name="AI товар двух складов",
            color="Red",
            weight_kg="50",
        )
        StockItem.objects.create(product=product, warehouse=main, bags=3)
        StockItem.objects.create(product=product, warehouse=secondary, bags=4)
        self.secondary_receipt = StockReceipt.objects.create(
            product=product,
            warehouse=secondary,
            bags=1,
        )
        self.main_receipt = StockReceipt.objects.create(
            product=product,
            warehouse=main,
            bags=1,
        )
        self.batch_id = Batch.objects.create(
            camera="cam3",
            warehouse=None,
            business_day=timezone.localdate(),
            scheduled_for=timezone.now(),
        ).pk
        self.product_id = product.pk
        self.secondary_id = secondary.pk

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_TO)
        self.apps = executor.loader.project_state(MIGRATE_TO).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_receipt_pins_legacy_batch_without_ambiguous_product_lookup(self):
        Posting = self.apps.get_model("cameras", "AlwaysOnStockPosting")
        Batch = self.apps.get_model("cameras", "AlwaysOnStockBatch")

        Posting.objects.create(
            batch_id=self.batch_id,
            color="red",
            product_id=self.product_id,
            detected_bags=1,
            posted_bags=1,
            receipt_id=self.secondary_receipt.pk,
        )

        assert Batch.objects.get(pk=self.batch_id).warehouse_id == self.secondary_id

    def test_batch_rejects_receipt_from_another_warehouse(self):
        if connection.vendor != "postgresql":
            self.skipTest("legacy batch guard is a PostgreSQL trigger")
        Posting = self.apps.get_model("cameras", "AlwaysOnStockPosting")

        Posting.objects.create(
            batch_id=self.batch_id,
            color="red",
            product_id=self.product_id,
            detected_bags=1,
            posted_bags=1,
            receipt_id=self.secondary_receipt.pk,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Posting.objects.create(
                batch_id=self.batch_id,
                color="blue",
                product_id=self.product_id,
                detected_bags=1,
                posted_bags=1,
                receipt_id=self.main_receipt.pk,
            )
