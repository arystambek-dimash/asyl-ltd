from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

MIGRATE_FROM = [
    ("accounts", "0003_user_must_change_password"),
    ("cameras", "0029_ai247_warehouse_route"),
    ("clients", "0017_backfill_unambiguous_departments"),
    ("orders", "0033_order_warehouse"),
    ("warehouse", "0010_multi_warehouse_stock_transfer"),
]
MIGRATE_TO = [
    ("accounts", "0003_user_must_change_password"),
    ("cameras", "0029_ai247_warehouse_route"),
    ("clients", "0017_backfill_unambiguous_departments"),
    ("orders", "0034_multi_warehouse_stock_guard"),
    ("warehouse", "0010_multi_warehouse_stock_transfer"),
]


class MultiWarehouseOrderGuardMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        apps = executor.loader.project_state(MIGRATE_FROM).apps
        User = apps.get_model("accounts", "User")
        Client = apps.get_model("clients", "Client")
        Order = apps.get_model("orders", "Order")
        Product = apps.get_model("catalog", "Product")
        StockItem = apps.get_model("warehouse", "StockItem")
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
            name="Товар двух складов",
            color="Blue",
            weight_kg="50",
        )
        StockItem.objects.create(product=product, warehouse=main, bags=8)
        StockItem.objects.create(product=product, warehouse=secondary, bags=5)
        user = User.objects.create(username="phase-two-order-client", is_client=True)
        client = Client.objects.create(user=user, phone="+77000000009")
        self.implicit_order_id = Order.objects.create(
            client=client,
            warehouse=None,
        ).pk
        self.explicit_order_id = Order.objects.create(
            client=client,
            warehouse=secondary,
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

    def test_explicit_order_warehouse_accepts_a_multi_warehouse_product(self):
        OrderItem = self.apps.get_model("orders", "OrderItem")
        Order = self.apps.get_model("orders", "Order")

        OrderItem.objects.create(
            order_id=self.explicit_order_id,
            product_id=self.product_id,
            quantity=1,
        )

        assert (
            Order.objects.get(pk=self.explicit_order_id).warehouse_id
            == self.secondary_id
        )

    def test_legacy_ambiguous_order_without_warehouse_fails_closed(self):
        if connection.vendor != "postgresql":
            self.skipTest("legacy order guard is a PostgreSQL trigger")
        OrderItem = self.apps.get_model("orders", "OrderItem")

        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(
                order_id=self.implicit_order_id,
                product_id=self.product_id,
                quantity=1,
            )
