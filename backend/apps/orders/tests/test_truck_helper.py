import pytest
from rest_framework.exceptions import ValidationError
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders import services


@pytest.fixture
def order(db):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    return Order.objects.create(client=c, status="paid")


def test_client_sets_then_only_client_can_change(order, make_user):
    client = make_user(username="cli", client=True)
    staff = make_user(username="stf")
    services.set_truck_number(order, "777ABC", client)
    assert order.truck_number == "777ABC"
    # staff cannot overwrite client's number
    assert services.can_set_truck_number(order, staff) is False
    with pytest.raises(ValidationError):
        services.set_truck_number(order, "111XXX", staff)
    # same client can
    services.set_truck_number(order, "222YYY", client)
    assert order.truck_number == "222YYY"


def test_staff_set_can_be_changed_by_staff(order, make_user):
    s1 = make_user(username="s1")
    s2 = make_user(username="s2")
    services.set_truck_number(order, "AAA", s1)
    assert services.can_set_truck_number(order, s2) is True
