from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.services import (
    finish_loading,
    record_arrival,
    record_count,
    record_shipment,
)
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


def test_shipment_sets_unpaid_debt(boss):
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    receive_stock(p, 100, boss)
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="confirmed", truck_number="01A1")
    OrderItem.objects.create(order=o, product=p, quantity=2)
    record_arrival(o, Decimal(8000), boss)
    record_count(o, 2, boss)
    finish_loading(o, boss)
    o.refresh_from_db()
    assert o.status == "loaded"
    assert o.is_debt is False

    record_shipment(o, boss)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.payment_status == "unpaid"
    debt_event = EventLog.objects.get(order=o, event_type="debt")
    assert debt_event.payload["intent"] == "debt"


def test_instant_settlement_shipment_is_not_logged_as_debt(boss):
    product = Product.objects.create(
        name="Instant settlement product",
        color="Blue",
        weight_kg="50",
        price="100.00",
    )
    receive_stock(product, 100, boss)
    client = Client.objects.create_with_user(
        first_name="Instant",
        last_name="Buyer",
        phone="instant-shipment-audit",
    )
    order = Order.objects.create(
        client=client,
        status="confirmed",
        truck_number="01-INSTANT",
        settlement_intent="instant",
        payment_method="cash",
    )
    OrderItem.objects.create(order=order, product=product, quantity=2)

    record_arrival(order, Decimal(8000), boss)
    record_count(order, 2, boss)
    finish_loading(order, boss)
    record_shipment(order, boss)

    assert not EventLog.objects.filter(order=order, event_type="debt").exists()
    shipment_event = EventLog.objects.get(order=order, event_type="shipment")
    assert "в долг" not in shipment_event.message
    assert shipment_event.payload["settlement_intent"] == "instant"
    assert shipment_event.payload["amount"] == str(order.total_amount)
