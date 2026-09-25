import pytest
from rest_framework.test import APIClient
from apps.clients.models import Client
from apps.notifications.models import Notification
from apps.notifications.services import notify
from apps.notifications.views import INBOX_LIMIT

pytestmark = pytest.mark.django_db


def test_client_lists_and_reads_notifications(client_user):
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x", user=client_user)
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


def test_inbox_is_capped_and_keeps_unread_on_top(client_user):
    """Колокольчик грузит список на каждой странице портала — выдача ограничена
    последними INBOX_LIMIT, и непрочитанные не вытесняются новыми прочитанными."""
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x", user=client_user)
    old_unread = notify(c, "Старое непрочитанное")
    for i in range(INBOX_LIMIT + 5):
        Notification.objects.create(client=c, text=f"Прочитано {i}", is_read=True)
    api = APIClient()
    api.force_authenticate(client_user)

    r = api.get("/api/portal/notifications/")
    assert r.status_code == 200
    assert len(r.data) == INBOX_LIMIT
    assert r.data[0]["id"] == old_unread.id
    assert all(item["is_read"] for item in r.data[1:])
    created = [item["created_at"] for item in r.data[1:]]
    assert created == sorted(created, reverse=True)

    # Отметить прочитанным можно и уведомление за пределами выдачи.
    oldest_read = Notification.objects.filter(client=c, is_read=True).order_by("created_at").first()
    assert api.post(f"/api/portal/notifications/{oldest_read.id}/read/").status_code == 200
