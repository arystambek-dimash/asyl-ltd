import pytest
from clients.models import Client

pytestmark = pytest.mark.django_db


def test_manager_creates_client_without_optional_fields(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/", {"name": "Лидер", "contact": "+998..."}
    )
    assert resp.status_code == 201
    c = Client.objects.get(name="Лидер")
    assert c.country == "" and c.requisites == ""


def test_country_and_requisites_optional(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/",
        {"name": "Эксп", "contact": "x", "country": "Узбекистан"},
    )
    assert resp.status_code == 201


def test_accountant_cannot_create_client(auth_client, accountant):
    resp = auth_client(accountant).post(
        "/api/clients/", {"name": "X", "contact": "y"}
    )
    assert resp.status_code == 403
