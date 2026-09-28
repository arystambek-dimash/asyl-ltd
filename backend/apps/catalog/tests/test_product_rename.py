"""Переименование товара видно в оформленных заказах и вагонах отгрузки."""
import importlib
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.services import archive_product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment, ShipmentWagon

pytestmark = pytest.mark.django_db

OLD_LABEL = "Высший сорт · Красный 50 кг"
MIGRATION = ("shipments", "0015_sync_product_label_snapshots")
migration = importlib.import_module("apps.shipments.migrations.0015_sync_product_label_snapshots")


@pytest.fixture
def product(make_product):
    return make_product(name="Высший сорт", color="Red", weight_kg="50")


@pytest.fixture
def client_row():
    return Client.objects.create_with_user(first_name="Дана", last_name="X", phone="rename")


def _item(client, product, *, status="shipped"):
    order = Order.objects.create(client=client, status=status)
    return OrderItem.objects.create(order=order, product=product, quantity=3, unit_price="7500.00")


def _wagon(item):
    shipment = Shipment.objects.create(order=item.order, bags_loaded=item.quantity, shipped_at=timezone.now())
    return ShipmentWagon.objects.create(
        shipment=shipment, number="28087658", product=item.product, bags=item.quantity,
        weight_kg=Decimal("150"), position=1)


def _labels(*rows):
    for row in rows:
        row.refresh_from_db()
    return [row.product_label for row in rows]


def test_rename_updates_old_orders_wagons_and_trash(auth_client, manager, product, client_row):
    shipped = _item(client_row, product)
    wagon = _wagon(shipped)
    trashed = _item(client_row, product)
    Order.all_objects.filter(pk=trashed.order_id).update(deleted_at=timezone.now())

    resp = auth_client(manager).patch(f"/api/products/{product.pk}/", {"name": "Первый сорт"}, format="json")

    assert resp.status_code == 200
    new_label = "Первый сорт · Красный 50 кг"
    assert _labels(shipped, wagon, trashed) == [new_label] * 3
    order = auth_client(manager).get(f"/api/orders/{shipped.order_id}/")
    assert order.status_code == 200
    assert order.data["items"][0]["product_label"] == new_label


def test_color_and_packaging_changes_do_not_rewrite_history(product, client_row):
    item = _item(client_row, product)
    wagon = _wagon(item)

    product.color, product.weight_kg = "Blue", Decimal("25")
    product.save()

    # Цвет и фасовка в подписи — как при заказе: по ним посчитаны камеры и тоннаж.
    assert _labels(item, wagon) == [OLD_LABEL] * 2
    assert (item.product_weight_kg_snapshot, item.product_cv_class_snapshot) == (Decimal("50.00"), "Red_50")
    assert wagon.product_weight_kg_snapshot == Decimal("50.00")
    assert item.order.total_amount == Decimal("22500.00")


def test_rename_keeps_the_ordered_colour_and_packaging(product, client_row):
    item = _item(client_row, product)
    product.name, product.color = "Первый сорт", "Blue"
    product.save()

    assert _labels(item) == ["Первый сорт · Красный 50 кг"]


def test_deleted_product_keeps_its_last_name(product, client_row):
    item = _item(client_row, product)
    wagon = _wagon(item)
    product.name = "Первый сорт"
    product.save()

    product.delete()

    assert _labels(item, wagon) == ["Первый сорт · Красный 50 кг"] * 2
    assert (item.product_id, wagon.product_id) == (None, None)


def test_rename_is_one_update_per_table_however_many_wagons(product, client_row):
    item = _item(client_row, product)
    shipment = Shipment.objects.create(order=item.order, bags_loaded=item.quantity, shipped_at=timezone.now())
    for position in range(1, 41):
        ShipmentWagon.objects.create(
            shipment=shipment, number=f"{28087600 + position:08d}", product=product, bags=1, weight_kg="50",
            position=position)
    product.name = "Первый сорт"

    with CaptureQueriesContext(connection) as queries:
        product.save()

    updates = [query["sql"] for query in queries.captured_queries if query["sql"].startswith("UPDATE")]
    assert len(updates) == 3  # сам товар, позиции заказов, вагоны
    assert set(ShipmentWagon.objects.values_list("product_label_snapshot", flat=True)) == {
        "Первый сорт · Красный 50 кг"}


def test_saves_that_keep_the_name_rewrite_nothing(product, client_row):
    item = _item(client_row, product)
    _wagon(item)

    with CaptureQueriesContext(connection) as queries:
        product.save()
        archive_product(product, None)
    # Без смены названия (архив, восстановление, фото) подписи в истории не трогаются.
    assert not any("orders_orderitem" in query["sql"] or "shipments_shipmentwagon" in query["sql"]
                   for query in queries.captured_queries)
    assert _labels(item) == [OLD_LABEL]


def test_migration_catches_up_products_renamed_before_it(product, make_product, client_row):
    item = _item(client_row, product)
    wagon = _wagon(item)
    other = make_product(name="Отруби", color="Green", weight_kg="25")
    other_item = _item(client_row, other)
    unsnapped = _item(client_row, product)
    deleted = _item(client_row, make_product(name="Снятый"))
    OrderItem.objects.filter(pk=unsnapped.pk).update(product_label_snapshot="")
    OrderItem.objects.filter(pk=deleted.pk).update(product=None, product_label_snapshot="Снятый · Красный 50 кг")
    # Переименование и смена цвета до этого релиза: товар новый, снимки старые.
    type(product).objects.filter(pk=product.pk).update(name="Первый сорт", color="Blue")

    migration.sync_product_label_snapshots(MigrationLoader(connection).project_state(MIGRATION).apps, None)

    assert _labels(item, wagon, other_item, deleted) == [
        "Первый сорт · Красный 50 кг", "Первый сорт · Красный 50 кг", "Отруби · Зелёный 25 кг",
        "Снятый · Красный 50 кг"]
    unsnapped.refresh_from_db()
    assert unsnapped.product_label_snapshot == ""
    assert item.product_weight_kg_snapshot == Decimal("50.00")
