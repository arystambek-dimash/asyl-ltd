import pytest

pytestmark = pytest.mark.django_db


def test_login_returns_tokens(api_client, make_user):
    make_user(username="boss", password="pass12345")
    resp = api_client.post(
        "/api/auth/login/", {"username": "boss", "password": "pass12345"}
    )
    assert resp.status_code == 200
    assert "access" in resp.data and "refresh" in resp.data


def test_login_requires_flagged_client_to_replace_temporary_password(
    api_client,
    make_user,
):
    user = make_user(
        username="temporary-client",
        password="Temporary-pass-2026!",
        client=True,
    )
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    response = api_client.post(
        "/api/auth/login/",
        {"username": user.username, "password": "Temporary-pass-2026!"},
        format="json",
    )

    assert response.status_code == 401
    assert response.data["code"] == "password_change_required"
    assert "access" not in response.data
    assert "refresh" not in response.data


def test_initial_password_replaces_temporary_password_and_returns_tokens(
    api_client,
    make_user,
):
    user = make_user(
        username="initial-password-client",
        password="Temporary-pass-2026!",
        client=True,
    )
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    response = api_client.post(
        "/api/auth/initial-password/",
        {
            "username": user.username,
            "current_password": "Temporary-pass-2026!",
            "new_password": "Fresh-portal-pass-2026!",
        },
        format="json",
    )

    assert response.status_code == 200
    assert "access" in response.data and "refresh" in response.data
    user.refresh_from_db()
    assert user.must_change_password is False
    assert user.check_password("Fresh-portal-pass-2026!")
    assert not user.check_password("Temporary-pass-2026!")

    me_client = api_client
    me_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert me_client.get("/api/auth/me/").status_code == 200


def test_initial_password_rejects_wrong_current_password(api_client, make_user):
    user = make_user(
        username="wrong-temporary-password",
        password="Temporary-pass-2026!",
        client=True,
    )
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    response = api_client.post(
        "/api/auth/initial-password/",
        {
            "username": user.username,
            "current_password": "Wrong-temporary-pass-2026!",
            "new_password": "Fresh-portal-pass-2026!",
        },
        format="json",
    )

    assert response.status_code == 401
    assert response.data["code"] == "invalid_credentials"
    user.refresh_from_db()
    assert user.must_change_password is True
    assert user.check_password("Temporary-pass-2026!")


def test_initial_password_rejects_reusing_temporary_password(api_client, make_user):
    user = make_user(
        username="same-temporary-password",
        password="Temporary-pass-2026!",
        client=True,
    )
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    response = api_client.post(
        "/api/auth/initial-password/",
        {
            "username": user.username,
            "current_password": "Temporary-pass-2026!",
            "new_password": "Temporary-pass-2026!",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "new_password" in response.data["detail"]
    user.refresh_from_db()
    assert user.must_change_password is True


def test_initial_password_applies_django_password_validation(api_client, make_user):
    user = make_user(
        username="weak-new-password",
        password="Temporary-pass-2026!",
        client=True,
    )
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    response = api_client.post(
        "/api/auth/initial-password/",
        {
            "username": user.username,
            "current_password": "Temporary-pass-2026!",
            "new_password": "12345678",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "new_password" in response.data["detail"]
    user.refresh_from_db()
    assert user.must_change_password is True


def test_initial_password_is_only_available_while_change_is_required(
    api_client,
    make_user,
):
    user = make_user(
        username="regular-client-password",
        password="Existing-pass-2026!",
        client=True,
    )

    response = api_client.post(
        "/api/auth/initial-password/",
        {
            "username": user.username,
            "current_password": "Existing-pass-2026!",
            "new_password": "Replacement-pass-2026!",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "password_change_not_required"
    user.refresh_from_db()
    assert user.check_password("Existing-pass-2026!")


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


def test_refresh_rejects_user_who_must_change_password(api_client, make_user):
    user = make_user(
        username="flagged-refresh-client",
        password="Temporary-pass-2026!",
        client=True,
    )
    refresh = api_client.post(
        "/api/auth/login/",
        {"username": user.username, "password": "Temporary-pass-2026!"},
        format="json",
    ).data["refresh"]
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    response = api_client.post(
        "/api/auth/refresh/",
        {"refresh": refresh},
        format="json",
    )

    assert response.status_code == 401
    assert response.data["code"] == "password_change_required"
