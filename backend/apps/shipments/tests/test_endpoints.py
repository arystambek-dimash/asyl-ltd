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


def test_arrive_without_weight_uses_estimated_load(boss):
    """Товар без флага веса: въезд без weigh_in_kg → расчётный вес по мешкам."""
    o = _order(boss, status="confirmed")  # 50 мешков × 50 кг = 2500
    r = _client(boss).post(f"/api/orders/{o.id}/arrive/", {}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "arrived"
    assert Decimal(o.shipment.weigh_in_kg) == Decimal("2500.00")


def test_arrive_with_weight_keeps_entered_value(boss):
    o = _order(boss, status="confirmed")
    r = _client(boss).post(f"/api/orders/{o.id}/arrive/",
                           {"weigh_in_kg": "9000"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert Decimal(o.shipment.weigh_in_kg) == Decimal("9000.00")


def test_ask_truck_weight_flag_exposed_on_order_item(boss):
    """Флаг товара доступен на позиции заказа (для поста погрузки)."""
    prod = Product.objects.create(name="Особый", color="Blue", weight_kg="50",
                                  price="100.00", ask_truck_weight=True)
    receive_stock(prod, 10, boss)
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="confirmed", truck_number="02B222")
    OrderItem.objects.create(order=o, product=prod, quantity=5)
    r = _client(boss).get(f"/api/orders/{o.id}/")
    assert r.status_code == 200
    assert r.data["items"][0]["ask_truck_weight"] is True


def test_loading_camera_assign_and_clear(operator):
    """Оператор занимает камеру под заказ и освобождает её."""
    prod = Product.objects.create(name="К", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="arrived", truck_number="03C333")
    OrderItem.objects.create(order=o, product=prod, quantity=2)
    r = _client(operator).post(f"/api/orders/{o.id}/loading-camera/", {"camera": "3"})
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.loading_camera == "cam3"  # normalize превратил "3" → "cam3"
    r = _client(operator).post(f"/api/orders/{o.id}/loading-camera/", {"camera": ""})
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.loading_camera == ""


def test_loading_camera_requires_shipping_load(manager):
    prod = Product.objects.create(name="К", color="Green", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="arrived", truck_number="04D444")
    OrderItem.objects.create(order=o, product=prod, quantity=1)
    # manager не имеет shipping.load
    r = _client(manager).post(f"/api/orders/{o.id}/loading-camera/", {"camera": "3"})
    assert r.status_code == 403


def test_shipping_action_cannot_access_other_department(operator, boss):
    o = _order(boss, status="confirmed")
    o.department = "field"
    o.client.department = "field"
    o.client.save(update_fields=["department"])
    o.save(update_fields=["department"])

    response = _client(operator).post(
        f"/api/orders/{o.id}/arrive/", {"weigh_in_kg": "8000"})

    assert response.status_code == 404
