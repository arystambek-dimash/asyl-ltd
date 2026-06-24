import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.warehouse.services import receive_stock
from apps.shipments.services import record_arrival, record_count

pytestmark = pytest.mark.django_db


def _order(boss, status="confirmed"):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
    receive_stock(prod, 100, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=50)
    return o


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_arrive_endpoint_no_truck_param(boss):
    o = _order(boss, status="confirmed")
    r = _client(boss).post(f"/api/orders/{o.id}/arrive/", {"weigh_in_kg": "8000"})
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "arrived"


def test_finish_loading_endpoint(boss):
    # confirmed → въезд → загрузка → finish (оплата теперь после shipped)
    o = _order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), boss)
    record_count(o, 50, boss)
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "loaded"


def test_finish_loading_wrong_status_400(boss):
    o = _order(boss, status="arrived")  # въезд есть, но загрузка не начата
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 400


def test_load_without_shipment_returns_400_and_keeps_status(boss):
    o = _order(boss, status="arrived")  # arrived but no Shipment row
    r = _client(boss).post(f"/api/orders/{o.id}/load/", {"bags": 10})
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.status == "arrived"
