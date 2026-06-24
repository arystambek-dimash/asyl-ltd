import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.warehouse.services import receive_stock
from apps.shipments.services import record_arrival, record_count, finish_loading, record_shipment

pytestmark = pytest.mark.django_db


def test_shipment_sets_unpaid_debt(boss):
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    receive_stock(p, 100, boss)
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="confirmed", truck_number="01A1")
    OrderItem.objects.create(order=o, product=p, quantity=2)
    record_arrival(o, Decimal("8000"), boss)
    record_count(o, 2, boss)
    finish_loading(o, boss)
    record_shipment(o, boss)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.payment_status == "unpaid"
