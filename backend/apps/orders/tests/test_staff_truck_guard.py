import pytest
from rest_framework.exceptions import ValidationError
from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders import services


@pytest.mark.django_db
def test_staff_cannot_overwrite_client_truck(manager, auth_client, make_user):
    cli = make_user(username="cli", client=True)
    c = Client.objects.create_with_user(user=cli, first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="confirmed")
    services.set_truck_number(o, "CLIENT777", cli)  # client owns the number
    r = auth_client(manager).patch(f"/api/orders/{o.id}/",
                                   {"truck_number": "STAFF111"}, format="json")
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.truck_number == "CLIENT777"


@pytest.mark.django_db
def test_staff_can_set_unset_truck(manager, auth_client):
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="confirmed")
    r = auth_client(manager).patch(f"/api/orders/{o.id}/",
                                   {"truck_number": "STAFF111"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.truck_number == "STAFF111"


@pytest.mark.django_db
def test_truck_number_change_rejects_starting_ai_session(manager):
    c = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="truck-open-ai",
    )
    order = Order.objects.create(
        client=c,
        status="confirmed",
        truck_number="OLD111",
        truck_number_set_by=manager,
    )
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=manager,
    )

    with pytest.raises(ValidationError) as exc:
        services.set_truck_number(order, "NEW222", manager)

    assert exc.value.detail["code"] == "truck_number_locked"
    order.refresh_from_db()
    assert order.truck_number == "OLD111"
