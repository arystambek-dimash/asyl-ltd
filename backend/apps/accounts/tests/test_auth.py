import pytest

pytestmark = pytest.mark.django_db


def test_login_returns_tokens(api_client, make_user):
    make_user(username="boss", password="pass12345")
    resp = api_client.post(
        "/api/auth/login/", {"username": "boss", "password": "pass12345"}
    )
    assert resp.status_code == 200
    assert "access" in resp.data and "refresh" in resp.data
