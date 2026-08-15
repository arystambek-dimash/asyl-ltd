from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.grain import scale as grain_scale
from apps.orders.models import Order, OrderItem
from apps.shipments.services import (
    begin_camera_loading,
    finish_ai_counting,
    record_arrival,
    record_shipment,
)
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


def _order(boss) -> tuple[Order, Product]:
    product = Product.objects.create(
        name="Товар независимого подсчёта",
        color="Blue",
        weight_kg="50",
        price="100.00",
    )
    receive_stock(product, 100, boss)
    client = Client.objects.create_with_user(
        first_name="Counting",
        last_name="Boundary",
        phone="counting-boundary",
    )
    order = Order.objects.create(
        client=client,
        status="confirmed",
        truck_number="01BOUNDARY",
    )
    OrderItem.objects.create(order=order, product=product, quantity=50)
    return order, product


def test_camera_loading_does_not_read_scale_or_record_arrival(
    boss, operator,
):
    order, product = _order(boss)

    with patch.object(
        grain_scale,
        "read_truck_scale",
        side_effect=AssertionError("Shipment counting must not read Grain scales"),
    ) as scale_read:
        begin_camera_loading(order, "cam2", operator)

    scale_read.assert_not_called()
    order.refresh_from_db()
    shipment = order.shipment
    assert order.status == "loading"
    assert order.loading_camera == "cam2"
    assert shipment.weigh_in_kg is None
    assert shipment.arrived_at is None
    assert shipment.shipped_at is None
    assert StockItem.objects.get(product=product).bags == 100
    assert not EventLog.objects.filter(order=order, event_type="arrival").exists()


@pytest.mark.parametrize(
    ("weigh_in_kg", "expected_weight"),
    [
        pytest.param(Decimal("8000"), Decimal("8000"), id="manual-weight"),
        pytest.param(None, Decimal("2500"), id="estimated-weight"),
    ],
)
def test_arrival_ai_count_and_ship_are_separate_transitions(
    boss,
    operator,
    weigh_in_kg,
    expected_weight,
):
    order, product = _order(boss)

    record_arrival(order, weigh_in_kg, operator)
    order.refresh_from_db()
    assert order.status == "arrived"
    assert order.shipment.weigh_in_kg == expected_weight

    begin_camera_loading(order, "cam2", operator)
    finish_ai_counting(order, 48, operator)

    order.refresh_from_db()
    shipment = order.shipment
    assert order.status == "loaded"
    assert order.loading_camera == ""
    assert shipment.bags_loaded == 48
    assert shipment.weigh_in_kg == expected_weight
    assert shipment.shipped_at is None
    assert StockItem.objects.get(product=product).bags == 100
    assert not EventLog.objects.filter(
        order=order,
        event_type__in=("debt", "shipment"),
    ).exists()

    record_shipment(order, operator)

    order.refresh_from_db()
    shipment.refresh_from_db()
    assert order.status == "shipped"
    assert shipment.shipped_at is not None
    assert shipment.bags_loaded == 48
    assert StockItem.objects.get(product=product).bags == 50
    assert EventLog.objects.filter(order=order, event_type="shipment").exists()
