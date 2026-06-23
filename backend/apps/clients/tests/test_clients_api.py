import pytest
from apps.clients.models import Client

pytestmark = pytest.mark.django_db


def test_manager_creates_client_without_optional_fields(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Иван", "last_name": "Петров", "phone": "+998..."},
    )
    assert resp.status_code == 201
    c = Client.objects.get(first_name="Иван")
    assert c.name == "Иван Петров"
    assert c.country == "" and c.iin == "" and c.bank == "" and c.bank_account == ""


def test_country_and_requisites_optional(auth_client, manager):
    resp = auth_client(manager).post(
        "/api/clients/",
        {"first_name": "Эксп", "last_name": "Орт", "phone": "x",
         "country": "Узбекистан"},
    )
    assert resp.status_code == 201


def test_accountant_cannot_create_client(auth_client, accountant):
    resp = auth_client(accountant).post(
        "/api/clients/", {"first_name": "X", "last_name": "Y", "phone": "z"}
    )
    assert resp.status_code == 403
