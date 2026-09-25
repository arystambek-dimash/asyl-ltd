"""Инварианты мультисклада в БД после contract-миграции (0011 / orders 0047 / cameras 0040)."""

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from apps.cameras.models import (
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
    AlwaysOnWarehouseRoute,
)
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.warehouse.models import StockItem, StockMovement, StockReceipt, Warehouse

ROLLOUT_FUNCTIONS = {
    "asyl_main_wh_id",
    "asyl_product_wh_id",
    "wh_compat_stockitem_warehouse",
    "wh_compat_protect_stockitem_delete",
    "wh_compat_product_event_warehouse",
    "orders_compat_pin_warehouse",
    "cameras_compat_pin_batch_warehouse",
}
CONTRACT_TRIGGERS = {
    "wh_stockitem_keep_warehouse_bu",
    "wh_receipt_requires_card_biu",
    "wh_movement_requires_card_biu",
    "wh_protect_main_warehouse_bud",
    "orders_item_requires_stock_card_biu",
    "cameras_posting_requires_stock_card_bi",
}
WAREHOUSE_COLUMNS = (
    StockItem,
    StockReceipt,
    StockMovement,
    Order,
    AlwaysOnStockBatch,
    AlwaysOnWarehouseRoute,
)


def _db_functions():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
        )
        return {row[0] for row in cursor.fetchall()}


def _db_triggers():
    with connection.cursor() as cursor:
        cursor.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
        return {row[0] for row in cursor.fetchall()}


@pytest.fixture
def main():
    return Warehouse.objects.get(code="main")


@pytest.fixture
def secondary():
    return Warehouse.objects.create(code="north", name="Северный склад")


@pytest.fixture
def product(make_product):
    return make_product()


def _raises_integrity(create):
    with pytest.raises(IntegrityError), transaction.atomic():
        create()


@pytest.mark.django_db
def test_warehouse_columns_are_mandatory():
    with connection.cursor() as cursor:
        for model in WAREHOUSE_COLUMNS:
            column = model._meta.get_field("warehouse").column
            description = connection.introspection.get_table_description(
                cursor, model._meta.db_table
            )
            nullable = {row.name: row.null_ok for row in description}
            assert nullable[column] is False, model._meta.label


@pytest.mark.django_db
def test_rollout_helpers_are_replaced_by_contract_guards():
    assert not ROLLOUT_FUNCTIONS & _db_functions()
    assert CONTRACT_TRIGGERS <= _db_triggers()


@pytest.mark.django_db
def test_stock_card_keeps_its_warehouse(main, secondary, product):
    item = StockItem.objects.create(product=product, warehouse=main, bags=1)

    _raises_integrity(
        lambda: StockItem.objects.filter(pk=item.pk).update(warehouse=secondary)
    )


@pytest.mark.django_db
def test_stock_event_needs_card_in_its_warehouse(main, secondary, product):
    StockItem.objects.create(product=product, warehouse=secondary, bags=4)

    _raises_integrity(
        lambda: StockReceipt.objects.create(product=product, warehouse=main, bags=1)
    )
    _raises_integrity(
        lambda: StockMovement.objects.create(
            product=product, warehouse=main, delta=1, balance_after=1
        )
    )
    StockReceipt.objects.create(product=product, warehouse=secondary, bags=1)
    StockMovement.objects.create(
        product=product, warehouse=secondary, delta=1, balance_after=5
    )


@pytest.mark.django_db
def test_order_item_needs_card_in_order_warehouse(
    make_user, main, secondary, product, make_product
):
    client = Client.objects.create(
        user=make_user(username="guard-client", client=True), phone="+77000000001"
    )
    order = Order.objects.create(client=client, warehouse=main)
    unstocked = make_product(name="Без карточки", color="Blue")
    StockItem.objects.create(product=product, warehouse=secondary, bags=4)

    _raises_integrity(
        lambda: OrderItem.objects.create(order=order, product=product, quantity=1)
    )
    OrderItem.objects.create(order=order, product=unstocked, quantity=1)


@pytest.mark.django_db
def test_ai_posting_stays_in_batch_warehouse(main, secondary, product):
    StockItem.objects.create(product=product, warehouse=main, bags=1)
    StockItem.objects.create(product=product, warehouse=secondary, bags=1)
    main_receipt = StockReceipt.objects.create(product=product, warehouse=main, bags=1)
    secondary_receipt = StockReceipt.objects.create(
        product=product, warehouse=secondary, bags=1
    )
    batch = AlwaysOnStockBatch.objects.create(
        camera="cam3",
        warehouse=secondary,
        business_day=timezone.localdate(),
        scheduled_for=timezone.now(),
    )

    def post(receipt, color):
        return AlwaysOnStockPosting.objects.create(
            batch=batch,
            color=color,
            product=product,
            detected_bags=1,
            posted_bags=1,
            receipt=receipt,
        )

    _raises_integrity(lambda: post(main_receipt, "red"))
    post(secondary_receipt, "red")


@pytest.mark.django_db
def test_main_warehouse_stays_protected(main, secondary):
    _raises_integrity(lambda: Warehouse.objects.filter(pk=main.pk).update(code="x"))
    _raises_integrity(
        lambda: Warehouse.objects.filter(pk=main.pk).update(is_active=False)
    )
    Warehouse.objects.filter(pk=main.pk).update(is_default=False)
    Warehouse.objects.filter(pk=secondary.pk).update(is_default=True)
    _raises_integrity(lambda: Warehouse.objects.filter(pk=secondary.pk).delete())


@pytest.mark.django_db
def test_product_delete_removes_its_stock_cards(main, product):
    StockItem.objects.create(product=product, warehouse=main, bags=0)

    product.delete()

    assert not StockItem.objects.filter(product_id=product.pk).exists()


class MultiWarehouseContractReversalTests(TransactionTestCase):
    before_contract = [
        ("cameras", "0039_remove_write_only_tech_fields"),
        ("orders", "0046_remove_dead_order_fields"),
        ("warehouse", "0010_multi_warehouse_stock_transfer"),
    ]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_contract_rolls_back_to_rollout_guards_and_forward_again(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before_contract)

        assert ROLLOUT_FUNCTIONS <= _db_functions()

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

        assert not ROLLOUT_FUNCTIONS & _db_functions()
        assert CONTRACT_TRIGGERS <= _db_triggers()
