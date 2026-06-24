import pytest
from rest_framework.test import APIClient
from apps.clients.models import Client
from apps.notifications.services import notify

pytestmark = pytest.mark.django_db


def test_client_lists_and_reads_notifications(client_user):
    c = Client.objects.create(first_name="A", last_name="B", phone="x", user=client_user)
    n = notify(c, "Ваш КАМАЗ 01A123 отправляется")
    api = APIClient()
    api.force_authenticate(client_user)

    r = api.get("/api/portal/notifications/")
    assert r.status_code == 200
    assert any(item["id"] == n.id for item in r.data)

    r2 = api.post(f"/api/portal/notifications/{n.id}/read/")
    assert r2.status_code == 200
    n.refresh_from_db()
    assert n.is_read is True
