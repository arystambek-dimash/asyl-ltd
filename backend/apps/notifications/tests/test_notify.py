import pytest
from apps.clients.models import Client
from apps.notifications.services import notify
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def test_notify_creates_unread():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    n = notify(c, "КАМАЗ 01A123 выехал")
    assert n.is_read is False
    assert Notification.objects.filter(client=c, text__icontains="01A123").exists()
