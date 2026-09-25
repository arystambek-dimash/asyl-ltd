import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.services import dispatch_order
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


def test_shipment_sets_unpaid_debt(boss):
    p = Product.objects.create(name="P", color="Red", weight_kg="50")
    receive_stock(p, 100, boss)
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="confirmed", truck_number="01A1")
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="100.00")
    assert o.is_debt is False

    dispatch_order(o, boss)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.payment_status == "unpaid"
    debt_event = EventLog.objects.get(order=o, event_type="debt")
    assert debt_event.payload["intent"] == "debt"
    # Долг — неоплаченный остаток, а не сумма заказа (предоплата в него не входит).
    assert debt_event.payload["amount"] == "200.00"


def test_instant_settlement_shipment_is_logged_as_debt(boss):
    """Товар уехал без оплаты — это долг клиента, даже если он собирался платить сразу."""
    product = Product.objects.create(
        name="Instant settlement product",
        color="Blue",
        weight_kg="50",
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
    OrderItem.objects.create(order=order, product=product, quantity=2, unit_price="100.00")

    dispatch_order(order, boss)

    debt_event = EventLog.objects.get(order=order, event_type="debt")
    assert debt_event.payload["intent"] == "instant"
    shipment_event = EventLog.objects.get(order=order, event_type="shipment")
    assert shipment_event.payload["settlement_intent"] == "instant"
    assert shipment_event.payload["amount"] == str(order.total_amount)
