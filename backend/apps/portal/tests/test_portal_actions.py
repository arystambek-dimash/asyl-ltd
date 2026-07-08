import pytest
from decimal import Decimal
from apps.clients.models import Client
from apps.catalog.models import Product
from apps.warehouse.models import StockItem
from apps.orders.models import Order, OrderItem


@pytest.fixture
def client_and_order(db, make_user):
    user = make_user(username="cli", client=True)
    c = Client.objects.create(user=user, first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    StockItem.objects.create(product=p, bags=500)
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=1)
    return user, o


def test_create_order_is_pending(db, make_user, auth_client):
    user = make_user(username="cli", client=True)
    Client.objects.create(user=user, first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    StockItem.objects.create(product=p, bags=500)
    r = auth_client(user).post("/api/portal/orders/",
                               {"items": [{"product": p.id, "quantity": 2}]}, format="json")
    assert r.status_code == 201
    assert Order.objects.get(id=r.data["id"]).status == "pending"


def test_pay_creates_pending_payment(client_and_order, auth_client):
    # Оплата доступна после отгрузки; заявка клиента встаёт в цепочку («принята»).
    user, o = client_and_order
    o.status = "shipped"; o.save()
    r = auth_client(user).post(f"/api/portal/orders/{o.id}/pay/", {"method": "kaspi"}, format="json")
    assert r.status_code == 201
    assert o.payments.filter(status="received", method="kaspi").exists()


def test_request_debt(client_and_order, auth_client):
    # Долг фиксируется после отгрузки.
    user, o = client_and_order
    o.status = "shipped"; o.save()
    r = auth_client(user).post(f"/api/portal/orders/{o.id}/request-debt/")
    assert r.status_code == 200
    o.refresh_from_db(); assert o.debt_requested is True


def test_pay_blocked_before_shipped(client_and_order, auth_client):
    user, o = client_and_order
    o.status = "arrived"; o.save()
    r = auth_client(user).post(f"/api/portal/orders/{o.id}/pay/", {"method": "kaspi"}, format="json")
    assert r.status_code == 400


def test_truck_blocked_before_confirmed(client_and_order, auth_client):
    # КАМАЗ вводится на статусе "confirmed"; до этого (pending) — нельзя.
    user, o = client_and_order
    o.status = "pending"; o.save()
    r = auth_client(user).patch(f"/api/portal/orders/{o.id}/truck/",
                                {"truck_number": "777"}, format="json")
    assert r.status_code == 409


def test_truck_set_when_confirmed(client_and_order, auth_client):
    user, o = client_and_order  # status confirmed
    r = auth_client(user).patch(f"/api/portal/orders/{o.id}/truck/",
                                {"truck_number": "777ABC"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db(); assert o.truck_number == "777ABC"


def test_cannot_touch_other_clients_order(db, make_user, auth_client):
    owner = make_user(username="owner", client=True)
    Client.objects.create(user=owner, first_name="O", last_name="W", phone="1")
    other = make_user(username="other", client=True)
    Client.objects.create(user=other, first_name="X", last_name="Y", phone="2")
    c = Client.objects.get(user=owner)
    o = Order.objects.create(client=c, status="confirmed")
    r = auth_client(other).post(f"/api/portal/orders/{o.id}/pay/", {"method": "card"}, format="json")
    assert r.status_code == 404
