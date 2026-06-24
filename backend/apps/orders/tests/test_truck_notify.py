import pytest
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders.services import set_truck_number
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def test_set_truck_number_notifies_client(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="confirmed")
    set_truck_number(o, "01A123", boss)
    assert Notification.objects.filter(client=c, text__icontains="01A123").exists()
