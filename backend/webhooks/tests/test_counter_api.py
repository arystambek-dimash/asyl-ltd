import pytest
import fakeredis
from rest_framework.test import APIClient
from webhooks.models import Camera
from webhooks import counter_store

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(counter_store, "_client", fakeredis.FakeRedis())


def _counter_cam():
    return Camera.objects.create(name="cnt", camera_id="counter-01", kind="counter",
                                 status="active", api_key="k", is_active=True)


def test_increment_grows_redis_not_order():
    cam = _counter_cam()
    c = APIClient()
    for _ in range(3):
        r = c.post("/api/webhook/camera/",
                   {"camera_id": "counter-01", "increment": 1},
                   format="json", HTTP_X_CAMERA_KEY="k")
        assert r.status_code == 200
    assert counter_store.get(cam.pk) == 3


from decimal import Decimal


def _paid_arrived_order(boss, plate="123ABC02", bags_stock=100, qty=50):
    from catalog.models import Grade, Packaging, Product
    from clients.models import Client
    from orders.models import Order, OrderItem, Payment
    from warehouse.services import receive_stock
    from shipments.services import record_arrival
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, bags_stock, boss)
    cl = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=cl, status="paid", truck_number=plate)
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    Payment.objects.create(order=o, amount=o.total_amount)
    record_arrival(o, Decimal("0"), boss)
    return o


def test_get_count(auth_client, make_user):
    u = make_user(username="v")
    from rbac.models import Permission, Role
    from employees.models import Employee
    role = Role.objects.create(name="r")
    p, _ = Permission.objects.get_or_create(
        code="cameras.view", defaults={"section": "cameras", "action": "view", "label": "x"})
    role.permissions.add(p)
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    cam = _counter_cam()
    counter_store.increment(cam.pk, by=12)
    r = auth_client(u).get(f"/api/count/{cam.pk}/")
    assert r.status_code == 200 and r.data["bags"] == 12


def test_close_writes_loading_and_resets(auth_client, make_user, boss):
    admin = make_user(username="root"); admin.is_superuser = True; admin.save()
    cam = _counter_cam()
    o = _paid_arrived_order(boss, plate="123ABC02")
    counter_store.increment(cam.pk, by=40)
    r = auth_client(admin).post(f"/api/count/{cam.pk}/close/", {"plate": "123 ABC 02"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "loading" and o.shipment.bags_loaded == 40
    assert counter_store.get(cam.pk) == 0
    from webhooks.models import CountSession
    assert CountSession.objects.filter(camera=cam, bags=40, order=o).exists()


def test_close_no_order_400_keeps_count(auth_client, make_user):
    admin = make_user(username="root2"); admin.is_superuser = True; admin.save()
    cam = _counter_cam()
    counter_store.increment(cam.pk, by=5)
    r = auth_client(admin).post(f"/api/count/{cam.pk}/close/", {"plate": "ZZZ"}, format="json")
    assert r.status_code == 400
    assert counter_store.get(cam.pk) == 5
