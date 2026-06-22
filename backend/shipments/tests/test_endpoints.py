import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock
from shipments.services import record_arrival, start_loading, record_count

pytestmark = pytest.mark.django_db


def _order(boss):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, 100, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status="paid", truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=50)
    Payment.objects.create(order=o, amount=o.total_amount)
    return o


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_arrive_endpoint_no_truck_param(boss):
    o = _order(boss)
    r = _client(boss).post(f"/api/orders/{o.id}/arrive/", {"weigh_in_kg": "8000"})
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "arrived"


def test_finish_loading_endpoint(boss):
    o = _order(boss)
    record_arrival(o, Decimal("8000"), boss)
    start_loading(o, boss)
    record_count(o, 50, boss)
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "loaded"


def test_finish_loading_wrong_status_400(boss):
    o = _order(boss)
    record_arrival(o, Decimal("8000"), boss)  # arrived, not loading
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 400
