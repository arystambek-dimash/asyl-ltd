import pytest
from django.contrib.auth import get_user_model
from clients.models import Client


@pytest.mark.django_db
def test_register_creates_client_user_and_returns_tokens(api_client):
    payload = {"username": "newcli", "password": "secret12345",
               "first_name": "Иван", "last_name": "Петров",
               "phone": "+77001112233", "iin": "990101300123"}
    r = api_client.post("/api/portal/register/", payload, format="json")
    assert r.status_code == 201
    assert "access" in r.data and "refresh" in r.data
    user = get_user_model().objects.get(username="newcli")
    assert user.is_client is True
    assert Client.objects.filter(user=user, first_name="Иван").exists()


@pytest.mark.django_db
def test_register_duplicate_username_rejected(api_client, make_user):
    make_user(username="taken")
    r = api_client.post("/api/portal/register/",
                        {"username": "taken", "password": "secret12345",
                         "first_name": "A", "last_name": "B", "phone": "1"},
                        format="json")
    assert r.status_code == 400
