"""Старые продажи и их физический флаг переживают смену модели и откат."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.clients.models import Client
from apps.warehouse.models import Warehouse


@pytest.mark.django_db(transaction=True)
def test_retired_shop_flag_preserves_history_and_allows_old_and_new_code(make_product):
    product = make_product(name="Историческая мука")
    client = Client.objects.create_with_user(first_name="Исторический магазин")
    warehouse = Warehouse.objects.get(code="main")
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_target = [("orders", "0060_order_is_shop_sale"), ("employees", "0012_employee_shop")]
    try:
        executor.migrate(old_target)
        old_apps = executor.loader.project_state(old_target).apps
        old_order = old_apps.get_model("orders", "Order").objects.create(
            client_id=client.pk, warehouse_id=warehouse.pk, status="shipped", is_shop_sale=True,
        )
        old_apps.get_model("orders", "OrderItem").objects.create(
            order_id=old_order.pk, product_id=product.pk, quantity=7, unit_price="500",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        new_apps = executor.loader.project_state(latest).apps
        Order = new_apps.get_model("orders", "Order")
        assert "is_shop_sale" not in {field.name for field in Order._meta.fields}
        assert Order.objects.get(pk=old_order.pk).status == "shipped"
        item = new_apps.get_model("orders", "OrderItem").objects.get(order_id=old_order.pk)
        assert (item.product_id, item.quantity, str(item.unit_price)) == (product.pk, 7, "500.00")
        assert new_apps.get_model("clients", "Client").objects.filter(pk=client.pk).exists()
        with connection.cursor() as cursor:
            cursor.execute("SELECT is_shop_sale FROM orders_order WHERE id = %s", [old_order.pk])
            assert cursor.fetchone() == (True,)
        new_order = Order.objects.create(client_id=client.pk, warehouse_id=warehouse.pk)
        with connection.cursor() as cursor:
            cursor.execute("SELECT is_shop_sale FROM orders_order WHERE id = %s", [new_order.pk])
            assert cursor.fetchone() == (False,)
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        restored = executor.loader.project_state(old_target).apps.get_model("orders", "Order")
        assert restored.objects.get(pk=old_order.pk).is_shop_sale is True
        assert restored.objects.get(pk=new_order.pk).is_shop_sale is False
    finally:
        MigrationExecutor(connection).migrate(latest)
