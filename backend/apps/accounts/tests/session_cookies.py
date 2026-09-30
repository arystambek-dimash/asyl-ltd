"""Проверки ответа, который начинает или заканчивает сессию входа."""

from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.credentials import REFRESH_COOKIE


def assert_session_started(response, user):
    """В теле только access, refresh — в HttpOnly-куке на /api/auth/ (как у логина)."""
    assert set(response.data) == {"access"}
    assert response["Cache-Control"] == "no-store"
    assert AccessToken(response.data["access"])["user_id"] == str(user.pk)
    cookie = response.cookies[REFRESH_COOKIE]
    assert cookie.value
    assert cookie["path"] == "/api/auth/"
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Strict"
    assert not cookie["secure"]  # локальные настройки; прод — test_auth_settings
    assert cookie["domain"] == ""
    assert 30 * 86400 - 5 <= int(cookie["max-age"]) <= 30 * 86400


def assert_session_ended(response, code):
    assert response.status_code == 401
    assert response.data["code"] == code
    cookie = response.cookies[REFRESH_COOKIE]
    assert cookie.value == ""
    assert int(cookie["max-age"]) == 0
    assert cookie["path"] == "/api/auth/"
    assert cookie["samesite"] == "Strict"
