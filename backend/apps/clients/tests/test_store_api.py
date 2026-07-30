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
    assert r.data["client_name"] == client.name
    assert r.data["payment_days"] == [5, 20]


def test_client_picker_exposes_only_form_reference_fields(manager):
    client = Client.objects.create(
        first_name="A",
        last_name="B",
        phone="secret",
        iin="123456789012",
        bank_account="KZSECRET",
    )

    response = _api(manager).get("/api/clients/picker/")

    assert response.status_code == 200
    assert response.data == [{"id": client.id, "name": client.name}]


def test_can_create_store_for_any_client(manager):
    foreign = Client.objects.create(
        first_name="Field", last_name="Client", phone="x")

    response = _api(manager).post("/api/stores/", {
        "client": foreign.id, "name": "Чужой магазин",
    }, format="json")

    assert response.status_code == 201
