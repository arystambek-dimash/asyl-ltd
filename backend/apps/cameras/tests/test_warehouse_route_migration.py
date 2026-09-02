from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


MIGRATE_FROM = [
    ("accounts", "0003_user_must_change_password"),
    ("cameras", "0028_separate_shipping_ai247_analytics"),
    ("warehouse", "0009_multi_warehouse_expand"),
]
MIGRATE_TO = [
    ("accounts", "0003_user_must_change_password"),
    ("cameras", "0029_ai247_warehouse_route"),
    ("warehouse", "0009_multi_warehouse_expand"),
]


class AlwaysOnWarehouseRouteMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        self.old_apps = executor.loader.project_state(MIGRATE_FROM).apps

        Warehouse = self.old_apps.get_model("warehouse", "Warehouse")
        Batch = self.old_apps.get_model("cameras", "AlwaysOnStockBatch")
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
        self.legacy_batch_id = Batch.objects.create(
            camera="cam3",
            business_day=timezone.localdate(),
            scheduled_for=timezone.now(),
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_TO)
        self.apps = executor.loader.project_state(MIGRATE_TO).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_batch_is_backfilled_to_main(self):
        Batch = self.apps.get_model("cameras", "AlwaysOnStockBatch")

        assert Batch.objects.get(pk=self.legacy_batch_id).warehouse_id == self.main.pk

    def test_rollback_posting_pins_batch_and_rejects_another_warehouse(self):
        if connection.vendor != "postgresql":
            self.skipTest("legacy posting guard is a PostgreSQL trigger")

        OldBatch = self.old_apps.get_model("cameras", "AlwaysOnStockBatch")
        OldPosting = self.old_apps.get_model("cameras", "AlwaysOnStockPosting")
        Product = self.apps.get_model("catalog", "Product")
        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        StockItem = self.apps.get_model("warehouse", "StockItem")
        StockReceipt = self.apps.get_model("warehouse", "StockReceipt")
        Batch = self.apps.get_model("cameras", "AlwaysOnStockBatch")

        secondary = Warehouse.objects.create(code="north", name="Северный склад")
        secondary_product = Product.objects.create(
            name="Северный товар",
            color="Blue",
            weight_kg="50",
        )
        main_product = Product.objects.create(
            name="Основной товар",
            color="Red",
            weight_kg="50",
        )
        StockItem.objects.create(
            warehouse=secondary,
            product=secondary_product,
            bags=3,
        )
        StockItem.objects.create(
            warehouse_id=self.main.pk,
            product=main_product,
            bags=3,
        )
        secondary_receipt = StockReceipt.objects.create(
            warehouse=secondary,
            product=secondary_product,
            bags=3,
        )
        main_receipt = StockReceipt.objects.create(
            warehouse_id=self.main.pk,
            product=main_product,
            bags=3,
        )
        rollback_batch = OldBatch.objects.create(
            camera="cam4",
            business_day=timezone.localdate(),
            scheduled_for=timezone.now(),
        )

        OldPosting.objects.create(
            batch_id=rollback_batch.pk,
            color="blue",
            product_id=secondary_product.pk,
            detected_bags=3,
            posted_bags=3,
            receipt_id=secondary_receipt.pk,
        )

        assert Batch.objects.get(pk=rollback_batch.pk).warehouse_id == secondary.pk
        with self.assertRaises(IntegrityError), transaction.atomic():
            OldPosting.objects.create(
                batch_id=rollback_batch.pk,
                color="red",
                product_id=main_product.pk,
                detected_bags=3,
                posted_bags=3,
                receipt_id=main_receipt.pk,
            )
        assert OldPosting.objects.filter(batch_id=rollback_batch.pk).count() == 1
