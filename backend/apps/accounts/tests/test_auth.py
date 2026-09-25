import pytest

pytestmark = pytest.mark.django_db

TEMPORARY_PASSWORD = "Temporary-pass-2026!"
NEW_PASSWORD = "Fresh-portal-pass-2026!"


@pytest.fixture
def temporary_client(make_user):
    """Клиент с временным паролем, который обязан его сменить."""
    user = make_user(username="temporary-client", password=TEMPORARY_PASSWORD, client=True)
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])
    return user


def _login(api_client, user, password):
    return api_client.post(
        "/api/auth/login/", {"username": user.username, "password": password}, format="json"
    )


def _initial_password(api_client, user, current=TEMPORARY_PASSWORD, new=NEW_PASSWORD):
    return api_client.post(
        "/api/auth/initial-password/",
        {"username": user.username, "current_password": current, "new_password": new},
        format="json",
    )


def test_login_returns_tokens(api_client, make_user):
    resp = _login(api_client, make_user(username="boss"), "pass12345")
    assert resp.status_code == 200
    assert "access" in resp.data and "refresh" in resp.data


def test_login_requires_flagged_client_to_replace_temporary_password(
    api_client,
    temporary_client,
):
    response = _login(api_client, temporary_client, TEMPORARY_PASSWORD)

    assert response.status_code == 401
    assert response.data["code"] == "password_change_required"
    assert "access" not in response.data
    assert "refresh" not in response.data


def test_initial_password_replaces_temporary_password_and_returns_tokens(
    api_client,
    temporary_client,
):
    response = _initial_password(api_client, temporary_client)

    assert response.status_code == 200
    assert "access" in response.data and "refresh" in response.data
    temporary_client.refresh_from_db()
    assert temporary_client.must_change_password is False
    assert temporary_client.check_password(NEW_PASSWORD)
    assert not temporary_client.check_password(TEMPORARY_PASSWORD)

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert api_client.get("/api/auth/me/").status_code == 200


def test_initial_password_rejects_wrong_current_password(api_client, temporary_client):
    response = _initial_password(api_client, temporary_client, current="Wrong-temporary-pass-2026!")

    assert response.status_code == 401
    assert response.data["code"] == "invalid_credentials"
    temporary_client.refresh_from_db()
    assert temporary_client.must_change_password is True
    assert temporary_client.check_password(TEMPORARY_PASSWORD)


@pytest.mark.parametrize(
    "new_password",
    [TEMPORARY_PASSWORD, "12345678"],
    ids=["reuses-temporary", "fails-django-validation"],
)
def test_initial_password_rejects_unacceptable_new_password(api_client, temporary_client, new_password):
    response = _initial_password(api_client, temporary_client, new=new_password)

    assert response.status_code == 400
    assert "new_password" in response.data["detail"]
    temporary_client.refresh_from_db()
    assert temporary_client.must_change_password is True


def test_initial_password_is_only_available_while_change_is_required(
    api_client,
    make_user,
):
    user = make_user(
        username="regular-client-password",
        password="Existing-pass-2026!",
        client=True,
    )

    response = _initial_password(api_client, user, current="Existing-pass-2026!")

    assert response.status_code == 400
    assert response.data["code"] == "password_change_not_required"
    user.refresh_from_db()
    assert user.check_password("Existing-pass-2026!")


def test_password_change_revokes_access_and_refresh_tokens(api_client, make_user):
    user = make_user(username="revoked", password="original-pass-123")
    tokens = _login(api_client, user, "original-pass-123").data
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
    refresh = _login(api_client, user, "original-pass-123").data["refresh"]
    user.delete()

    response = api_client.post(
        "/api/auth/refresh/",
        {"refresh": refresh},
        format="json",
    )

    assert response.status_code == 401


def test_refresh_rejects_user_who_must_change_password(api_client, make_user):
    user = make_user(username="flagged-refresh-client", password=TEMPORARY_PASSWORD, client=True)
    refresh = _login(api_client, user, TEMPORARY_PASSWORD).data["refresh"]
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    response = api_client.post(
        "/api/auth/refresh/",
        {"refresh": refresh},
        format="json",
    )

    assert response.status_code == 401
    assert response.data["code"] == "password_change_required"
