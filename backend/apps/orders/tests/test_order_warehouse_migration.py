from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

MIGRATE_FROM = [
    ("accounts", "0003_user_must_change_password"),
    ("clients", "0017_backfill_unambiguous_departments"),
    ("orders", "0032_remove_order_scale_weighing_required"),
    ("warehouse", "0009_multi_warehouse_expand"),
]
MIGRATE_TO = [
    ("accounts", "0003_user_must_change_password"),
    ("clients", "0017_backfill_unambiguous_departments"),
    ("orders", "0033_order_warehouse"),
    ("warehouse", "0009_multi_warehouse_expand"),
]


class OrderWarehouseMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        self.old_apps = executor.loader.project_state(MIGRATE_FROM).apps

        User = self.old_apps.get_model("accounts", "User")
        Client = self.old_apps.get_model("clients", "Client")
        Order = self.old_apps.get_model("orders", "Order")
        Warehouse = self.old_apps.get_model("warehouse", "Warehouse")

        # TransactionTestCase flushes rows but keeps migration history. Seed the
        # prerequisite warehouse contract explicitly so this test is stable no
        # matter which migration test ran before it.
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
        self.main_id = main.pk
        user = User.objects.create(
            username="order-warehouse-migration-client",
            is_client=True,
        )
        client = Client.objects.create(user=user, phone="+77000000001")
        self.client_id = client.pk
        self.order_id = Order.objects.create(client=client).pk

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_TO)
        self.apps = executor.loader.project_state(MIGRATE_TO).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_orders_are_backfilled_to_stable_main_warehouse(self):
        Order = self.apps.get_model("orders", "Order")
        Warehouse = self.apps.get_model("warehouse", "Warehouse")

        main = Warehouse.objects.get(code="main")
        assert main.pk == self.main_id
        assert Order.objects.get(pk=self.order_id).warehouse_id == main.pk

    def test_previous_image_can_still_insert_an_order_without_warehouse(self):
        OldOrder = self.old_apps.get_model("orders", "Order")
        NewOrder = self.apps.get_model("orders", "Order")

        rollback_insert = OldOrder.objects.create(client_id=self.client_id)

        assert NewOrder.objects.get(pk=rollback_insert.pk).warehouse_id is None
        field = NewOrder._meta.get_field("warehouse")
        assert field.null is True
        assert field.remote_field.on_delete is models.PROTECT

    def test_previous_image_first_item_pins_order_and_mixed_warehouse_fails(self):
        if connection.vendor != "postgresql":
            self.skipTest("legacy order guard is a PostgreSQL trigger")

        OldProduct = self.old_apps.get_model("catalog", "Product")
        OldOrder = self.old_apps.get_model("orders", "Order")
        OldOrderItem = self.old_apps.get_model("orders", "OrderItem")
        NewOrder = self.apps.get_model("orders", "Order")
        Warehouse = self.apps.get_model("warehouse", "Warehouse")
        StockItem = self.apps.get_model("warehouse", "StockItem")

        secondary = Warehouse.objects.create(code="north", name="Северный склад")
        secondary_product = OldProduct.objects.create(
            name="Северный товар",
            color="Blue",
            weight_kg="50",
        )
        main_product = OldProduct.objects.create(
            name="Основной товар",
            color="Red",
            weight_kg="50",
        )
        StockItem.objects.create(
            warehouse_id=secondary.pk,
            product_id=secondary_product.pk,
            bags=5,
        )
        StockItem.objects.create(
            warehouse_id=self.main_id,
            product_id=main_product.pk,
            bags=5,
        )
        rollback_order = OldOrder.objects.create(client_id=self.client_id)

        OldOrderItem.objects.create(
            order_id=rollback_order.pk,
            product_id=secondary_product.pk,
            quantity=1,
        )

        assert (
            NewOrder.objects.get(pk=rollback_order.pk).warehouse_id == secondary.pk
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            OldOrderItem.objects.create(
                order_id=rollback_order.pk,
                product_id=main_product.pk,
                quantity=1,
            )
        assert OldOrderItem.objects.filter(order_id=rollback_order.pk).count() == 1
