import pytest
from rest_framework.test import APIClient
from apps.clients.models import Client

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_create_store_via_api(manager):
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    r = _api(manager).post("/api/stores/", {
        "client": client.id, "name": "Магазин №1",
        "payment_schedule_type": "monthly", "payment_days": [5, 20],
    }, format="json")
    assert r.status_code == 201
    assert r.data["name"] == "Магазин №1"
    assert r.data["payment_days"] == [5, 20]
