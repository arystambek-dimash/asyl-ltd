"""Станция вагонов у заказа: колонка с DEFAULT в базе переживает откат образа."""
import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.clients.models import Client
from apps.orders.models import Order
from apps.warehouse.models import Warehouse

pytestmark = pytest.mark.django_db


def test_rail_station_keeps_database_default_for_old_image_rollback():
    loader = MigrationLoader(connection)
    migration_order = loader.project_state([("orders", "0044_order_rail_station")]).apps.get_model("orders", "Order")
    assert Order._meta.get_field("rail_station").db_default == ""
    assert migration_order._meta.get_field("rail_station").db_default == ""

    # Автооткат возвращает прошлый образ без отката миграций: его ORM не знает
    # колонку и не пишет её в INSERT — значение даёт DEFAULT базы.
    old_order = loader.project_state([("orders", "0043_backfill_truck_numbers")]).apps.get_model("orders", "Order")
    client = Client.objects.create_with_user(first_name="Старый", phone="1")
    created = old_order.objects.create(
        client_id=client.pk,
        status="confirmed",
        transport_type="train",
        warehouse_id=Warehouse.objects.get(code="main").pk,
    )

    assert Order.objects.get(pk=created.pk).rail_station == ""
