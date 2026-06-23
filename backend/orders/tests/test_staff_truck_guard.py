import pytest
from clients.models import Client
from orders.models import Order
from orders import services


@pytest.mark.django_db
def test_staff_cannot_overwrite_client_truck(manager, auth_client, make_user):
    cli = make_user(username="cli", client=True)
    c = Client.objects.create(user=cli, first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="paid")
    services.set_truck_number(o, "CLIENT777", cli)  # client owns the number
    r = auth_client(manager).patch(f"/api/orders/{o.id}/",
                                   {"truck_number": "STAFF111"}, format="json")
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.truck_number == "CLIENT777"


@pytest.mark.django_db
def test_staff_can_set_unset_truck(manager, auth_client):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="paid")
    r = auth_client(manager).patch(f"/api/orders/{o.id}/",
                                   {"truck_number": "STAFF111"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.truck_number == "STAFF111"
