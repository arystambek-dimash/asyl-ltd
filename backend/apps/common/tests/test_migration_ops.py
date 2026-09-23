"""Новые таблицы повторяют on_delete в базе: старый образ после отката удаляет строки без них."""

import pytest
from django.db import connection

from apps.bots.models import BotClientProfile
from apps.catalog.models import Product, ProductAlias
from apps.clients.models import Client
from apps.common.migration_ops import db_on_delete
from apps.orders.models import Order
from apps.shipments.models import Shipment, ShipmentWagon

pytestmark = pytest.mark.django_db

# pg_constraint.confdeltype: c — CASCADE, n — SET NULL.
NEW_FOREIGN_KEYS = [
    (ShipmentWagon, "shipment", "c"),
    (ShipmentWagon, "product", "n"),
    (ProductAlias, "product", "c"),
    (ProductAlias, "created_by", "n"),
    (BotClientProfile, "client", "c"),
    (BotClientProfile, "created_by", "n"),
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


def test_only_cascade_and_set_null_are_supported():
    with pytest.raises(ValueError):
        db_on_delete("shipments", "ShipmentWagon", "shipment", "RESTRICT; DROP TABLE x")
