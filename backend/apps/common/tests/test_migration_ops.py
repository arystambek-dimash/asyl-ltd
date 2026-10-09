"""Новые таблицы повторяют on_delete в базе: старый образ после отката удаляет строки без них."""

import pytest
from django.db import connection

from apps.bots.models import BotClientProfile
from apps.catalog.models import Product, ProductAlias
from apps.clients.models import Client
from apps.common.migration_ops import db_on_delete
from apps.orders.models import GoodsReturn, GoodsReturnItem, Order
from apps.shipments.models import Shipment, ShipmentSource, ShipmentWagon
from apps.warehouse.models import Warehouse

pytestmark = pytest.mark.django_db

# pg_constraint.confdeltype: c — CASCADE, n — SET NULL.
NEW_FOREIGN_KEYS = [
    (ShipmentWagon, "shipment", "c"),
    (ShipmentWagon, "product", "n"),
    (ShipmentSource, "shipment", "c"),
    (ShipmentSource, "product", "n"),
    (ProductAlias, "product", "c"),
    (ProductAlias, "created_by", "n"),
    (BotClientProfile, "client", "c"),
    (BotClientProfile, "created_by", "n"),
    (GoodsReturnItem, "goods_return", "c"),
    (GoodsReturnItem, "product", "n"),
    (GoodsReturn, "accepted_by", "n"),
]


def _raw(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


@pytest.mark.parametrize(("model", "field_name", "action"), NEW_FOREIGN_KEYS)
def test_new_tables_repeat_on_delete_in_the_database(model, field_name, action):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT con.confdeltype, con.condeferrable, con.condeferred
            FROM pg_constraint con
            JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
            WHERE con.contype = 'f' AND con.conrelid = %s::regclass AND att.attname = %s
            """,
            [model._meta.db_table, model._meta.get_field(field_name).column],
        )
        assert cursor.fetchall() == [(action, True, True)]


def test_old_image_rollback_of_a_report_shipment_takes_its_wagons_along():
    """Старый ``rollback_shipment`` делает ``shipment.delete()`` — вагонов он не знает."""
    client = Client.objects.create_with_user(first_name="Osiyo", phone="+998 90 000")
    shipment = Shipment.objects.create(order=Order.objects.create(client=client, transport_type="train"))
    product = Product.objects.create(name="Д1с", color="Red", weight_kg="50")
    kept = ShipmentWagon.objects.create(
        shipment=Shipment.objects.create(order=Order.objects.create(client=client, transport_type="train")),
        number="28087666", product=product, bags=1360, weight_kg="68000", position=1)
    ShipmentWagon.objects.create(
        shipment=shipment, number="28087658", product=product, bags=1360, weight_kg="68000", position=1)
    ProductAlias.objects.create(code="Д1с", product=product)

    _raw("DELETE FROM shipments_shipment WHERE id = %s", [shipment.pk])
    _raw("DELETE FROM catalog_product WHERE id = %s", [product.pk])
    _raw("SET CONSTRAINTS ALL IMMEDIATE")  # проверка ключей, как при коммите

    assert list(ShipmentWagon.objects.values_list("pk", "product_id")) == [(kept.pk, None)]
    assert not ProductAlias.objects.exists()


def test_old_image_rollback_takes_shipment_sources_along():
    """Старый образ не знает складов-источников: удаляет Shipment и товар сырым SQL."""
    client = Client.objects.create_with_user(first_name="Osiyo", phone="+998 90 000")
    main = Warehouse.objects.get(code="main")
    first = Product.objects.create(name="Д1с", color="Red", weight_kg="50")
    second = Product.objects.create(name="Б", color="Blue", weight_kg="25")
    rolled_back = Shipment.objects.create(order=Order.objects.create(client=client))
    ShipmentSource.objects.create(shipment=rolled_back, product=first, warehouse=main, bags=20)
    kept = Shipment.objects.create(order=Order.objects.create(client=client))
    kept_first = ShipmentSource.objects.create(shipment=kept, product=first, warehouse=main, bags=12)
    kept_second = ShipmentSource.objects.create(shipment=kept, product=second, warehouse=main, bags=8)

    _raw("DELETE FROM shipments_shipment WHERE id = %s", [rolled_back.pk])
    _raw("DELETE FROM catalog_product WHERE id IN (%s, %s)", [first.pk, second.pk])
    _raw("SET CONSTRAINTS ALL IMMEDIATE")  # проверка ключей, как при коммите

    # Два товара одного склада после SET NULL не спорят за уникальную ячейку: NULL различны.
    assert sorted(ShipmentSource.objects.values_list("pk", "product_id", "warehouse_id", "bags")) == [
        (kept_first.pk, None, main.pk, 12),
        (kept_second.pk, None, main.pk, 8),
    ]


def test_only_cascade_and_set_null_are_supported():
    with pytest.raises(ValueError):
        db_on_delete("shipments", "ShipmentWagon", "shipment", "RESTRICT; DROP TABLE x")
