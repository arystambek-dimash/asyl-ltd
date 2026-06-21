import pytest

pytestmark = pytest.mark.django_db


def test_me_returns_roles(auth_client, boss):
    resp = auth_client(boss).get("/api/auth/me/")
    assert resp.status_code == 200
    assert resp.data["username"] == "boss"
    assert "boss" in resp.data["roles"]
    assert resp.data["is_client"] is False


def test_me_for_client_includes_client_id(auth_client, client_user):
    from clients.models import Client
    c = Client.objects.create(first_name="Мой", last_name="К", phone="x", user=client_user)
    resp = auth_client(client_user).get("/api/auth/me/")
    assert resp.status_code == 200
    assert resp.data["is_client"] is True
    assert resp.data["client_id"] == c.id
    assert resp.data["roles"] == []


def test_me_requires_auth(api_client):
    resp = api_client.get("/api/auth/me/")
    assert resp.status_code == 401
