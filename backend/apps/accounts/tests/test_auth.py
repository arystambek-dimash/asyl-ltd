import pytest

pytestmark = pytest.mark.django_db


def test_login_returns_tokens(api_client, make_user):
    make_user(username="boss", password="pass12345")
    resp = api_client.post(
        "/api/auth/login/", {"username": "boss", "password": "pass12345"}
    )
    assert resp.status_code == 200
    assert "access" in resp.data and "refresh" in resp.data


def test_password_change_revokes_access_and_refresh_tokens(api_client, make_user):
    user = make_user(username="revoked", password="original-pass-123")
    tokens = api_client.post(
        "/api/auth/login/",
        {"username": user.username, "password": "original-pass-123"},
        format="json",
    ).data
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert api_client.get("/api/auth/me/").status_code == 200

    user.set_password("replacement-pass-123")
    user.save(update_fields=["password"])

    assert api_client.get("/api/auth/me/").status_code == 401
    api_client.credentials()
    assert api_client.post(
        "/api/auth/refresh/",
        {"refresh": tokens["refresh"]},
        format="json",
    ).status_code == 401


def test_deleted_user_refresh_token_returns_401(api_client, make_user):
    user = make_user(username="deleted-token-user", password="original-pass-123")
    refresh = api_client.post(
        "/api/auth/login/",
        {"username": user.username, "password": "original-pass-123"},
        format="json",
    ).data["refresh"]
    user.delete()

    response = api_client.post(
        "/api/auth/refresh/",
        {"refresh": refresh},
        format="json",
    )

    assert response.status_code == 401
