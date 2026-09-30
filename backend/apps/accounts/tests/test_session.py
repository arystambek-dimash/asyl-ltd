"""Сессия входа: refresh только в HttpOnly-куке, ротация, окно преемника, выход, Origin."""

import hashlib
import re
import threading
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib import admin
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from apps.accounts.credentials import REFRESH_COOKIE, SessionRefreshToken
from apps.accounts.tests.session_cookies import (
    assert_session_ended,
    assert_session_started,
)
from apps.cameras.api_views.access import CAM_COOKIE

pytestmark = pytest.mark.django_db

ORIGIN = "http://testserver"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(make_user):
    return make_user(username="session-user", password="pass12345")


def _login(client, user):
    response = client.post(
        "/api/auth/login/",
        {"username": user.username, "password": "pass12345"},
        format="json",
    )
    assert response.status_code == 200
    return response.cookies[REFRESH_COOKIE].value


def _refresh(client):
    return client.post("/api/auth/refresh/", {}, format="json")


def _logout(client):
    return client.post("/api/auth/logout/", {}, format="json")


def _client_with(raw):
    client = APIClient(HTTP_ORIGIN=ORIGIN)
    client.cookies[REFRESH_COOKIE] = raw
    return client


def _aged_refresh(user, age=timedelta(hours=2)):
    """Refresh, выданный ``age`` назад (строка в OutstandingToken уже есть)."""
    token = SessionRefreshToken.for_user(user)
    token.set_iat(at_time=token.current_time - age)
    token.set_exp(from_time=token.current_time - age)
    return str(token)


def _claims(raw):
    return token_backend.decode(raw, verify=True)


def _blacklisted(raw):
    return BlacklistedToken.objects.filter(token__jti=_claims(raw)["jti"]).exists()


def test_refresh_returns_access_and_keeps_a_fresh_cookie(api_client, user):
    raw = _login(api_client, user)

    response = _refresh(api_client)

    assert response.status_code == 200
    assert_session_started(response, user)
    # Моложе часа — без ротации: та же кука, ни одной новой строки.
    assert response.cookies[REFRESH_COOKIE].value == raw
    assert OutstandingToken.objects.count() == 1
    assert not BlacklistedToken.objects.exists()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert api_client.get("/api/auth/me/").status_code == 200


def test_refresh_reads_only_the_cookie(api_client, user):
    raw = _login(APIClient(HTTP_ORIGIN=ORIGIN), user)

    response = api_client.post("/api/auth/refresh/", {"refresh": raw}, format="json")

    assert_session_ended(response, "no_session")


def test_refresh_older_than_an_hour_rotates_for_thirty_more_days(user):
    old = _aged_refresh(user)

    response = _refresh(_client_with(old))

    assert response.status_code == 200
    assert_session_started(response, user)
    new = response.cookies[REFRESH_COOKIE].value
    assert new != old
    assert _blacklisted(old) and not _blacklisted(new)
    assert OutstandingToken.objects.count() == 2
    old_claims, new_claims = _claims(old), _claims(new)
    assert new_claims["sid"] == old_claims["sid"]
    assert new_claims["user_id"] == str(user.pk)
    assert new_claims["hash_password"] == old_claims["hash_password"]
    assert new_claims["exp"] > old_claims["exp"]


def test_rotated_refresh_replayed_within_grace_gets_the_same_successor(user):
    old = _aged_refresh(user)
    first = _refresh(_client_with(old))
    successor = first.cookies[REFRESH_COOKIE].value

    # Вторая вкладка с той же старой кукой или потерянный ответ.
    replay = _refresh(_client_with(old))

    assert replay.status_code == 200
    assert replay.cookies[REFRESH_COOKIE].value == successor
    assert _claims(replay.data["access"])["sid"] == _claims(old)["sid"]
    assert OutstandingToken.objects.count() == 2
    assert BlacklistedToken.objects.count() == 1


def test_rotated_refresh_after_grace_ends_the_session(user):
    old = _aged_refresh(user)
    assert _refresh(_client_with(old)).status_code == 200
    cache.clear()  # окно преемника истекло

    assert_session_ended(_refresh(_client_with(old)), "token_not_valid")


def test_grace_successor_is_still_revoked_by_a_password_change(user):
    old = _aged_refresh(user)
    assert _refresh(_client_with(old)).status_code == 200
    user.set_password("replacement-pass-123")
    user.save(update_fields=["password"])

    assert_session_ended(_refresh(_client_with(old)), "password_changed")


@pytest.mark.django_db(transaction=True)
def test_parallel_refreshes_with_one_old_cookie_share_one_successor(make_user):
    user = make_user(username="parallel-tabs", password="pass12345")
    old = _aged_refresh(user)
    barrier = threading.Barrier(2)
    responses = []

    def tab():
        client = _client_with(old)
        barrier.wait()
        try:
            responses.append(_refresh(client))
        finally:
            connection.close()

    tabs = [threading.Thread(target=tab) for _ in range(2)]
    for thread in tabs:
        thread.start()
    for thread in tabs:
        thread.join(timeout=30)

    assert [response.status_code for response in responses] == [200, 200]
    cookies = {response.cookies[REFRESH_COOKIE].value for response in responses}
    assert len(cookies) == 1 and old not in cookies
    assert OutstandingToken.objects.count() == 2
    assert BlacklistedToken.objects.count() == 1


def test_invalid_refresh_cookie_ends_the_session(user):
    assert_session_ended(_refresh(_client_with("not-a-jwt")), "token_not_valid")
    access = str(SessionRefreshToken.for_user(user).access_token)
    assert_session_ended(_refresh(_client_with(access)), "token_not_valid")
    idle = _aged_refresh(user, age=timedelta(days=31))  # 30 дней простоя
    assert_session_ended(_refresh(_client_with(idle)), "token_not_valid")


def test_refresh_without_session_id_is_not_accepted_or_extended(user):
    # Refresh до сессий в куке: без sid, на сутки, строки в OutstandingToken нет.
    issued = timezone.now() - timedelta(hours=2)
    legacy = token_backend.encode(
        {
            "token_type": "refresh",
            "jti": uuid4().hex,
            "user_id": str(user.pk),
            "hash_password": get_md5_hash_password(user.password),
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(days=1)).timestamp()),
        }
    )
    jti = _claims(legacy)["jti"]

    assert_session_ended(_refresh(_client_with(legacy)), "token_not_valid")
    assert _logout(_client_with(legacy)).status_code == 204

    assert not OutstandingToken.objects.filter(jti=jti).exists()
    assert not BlacklistedToken.objects.exists()


def test_every_login_is_a_new_session_id(api_client, user):
    first = _claims(_login(api_client, user))
    second = _claims(_login(APIClient(HTTP_ORIGIN=ORIGIN), user))

    assert first["sid"] and second["sid"] and first["sid"] != second["sid"]
    assert first["user_id"] == second["user_id"] == str(user.pk)


def test_access_token_carries_user_and_session_ids(api_client, user):
    raw = _login(api_client, user)
    access = AccessToken(_refresh(api_client).data["access"])

    assert access["user_id"] == str(user.pk)
    assert access["sid"] == _claims(raw)["sid"]


def test_outstanding_tokens_store_only_a_digest(api_client, user):
    login = _login(api_client, user)
    rotated = _refresh(_client_with(_aged_refresh(user))).cookies[REFRESH_COOKIE].value

    rows = {row.jti: row for row in OutstandingToken.objects.all()}
    assert len(rows) == 3
    for raw in (login, rotated):
        assert rows[_claims(raw)["jti"]].token == hashlib.sha256(raw.encode()).hexdigest()
    for row in rows.values():
        assert re.fullmatch(r"[0-9a-f]{64}", row.token)
        assert row.user_id == user.pk


def test_token_tables_are_not_in_the_admin():
    assert not admin.site.is_registered(OutstandingToken)
    assert not admin.site.is_registered(BlacklistedToken)


def test_logout_revokes_the_session_and_clears_both_cookies(api_client, user):
    raw = _login(api_client, user)
    api_client.cookies[CAM_COOKIE] = "camera-cookie"

    response = _logout(api_client)

    assert response.status_code == 204
    assert _blacklisted(raw)
    refresh_cookie = response.cookies[REFRESH_COOKIE]
    assert refresh_cookie.value == "" and int(refresh_cookie["max-age"]) == 0
    assert refresh_cookie["path"] == "/api/auth/"
    assert refresh_cookie["samesite"] == "Strict"
    assert refresh_cookie["httponly"] is True
    camera_cookie = response.cookies[CAM_COOKIE]
    assert camera_cookie.value == "" and int(camera_cookie["max-age"]) == 0
    assert camera_cookie["path"] == "/go2rtc/"
    assert camera_cookie["samesite"] == "Lax"
    assert_session_ended(_refresh(_client_with(raw)), "token_not_valid")


def test_logout_is_idempotent_and_needs_no_login(api_client, user):
    assert _logout(api_client).status_code == 204  # без куки
    raw = _login(api_client, user)
    assert _logout(_client_with(raw)).status_code == 204
    assert _logout(_client_with(raw)).status_code == 204  # уже отозван
    assert _logout(_client_with("not-a-jwt")).status_code == 204
    assert BlacklistedToken.objects.count() == 1


def test_logout_with_a_just_rotated_cookie_also_revokes_its_successor(user):
    old = _aged_refresh(user)
    successor = _refresh(_client_with(old)).cookies[REFRESH_COOKIE].value

    # Ответ с преемником не дошёл: в браузере осталась старая кука.
    assert _logout(_client_with(old)).status_code == 204

    assert _blacklisted(old) and _blacklisted(successor)
    assert_session_ended(_refresh(_client_with(successor)), "token_not_valid")
    assert_session_ended(_refresh(_client_with(old)), "token_not_valid")


@pytest.mark.parametrize(
    "origin",
    [None, "null", "https://evil.example", "https://testserver", "http://testserver:8080"],
    ids=["missing", "null", "foreign", "other-scheme", "other-port"],
)
@pytest.mark.parametrize(
    ("url", "data"),
    [
        ("/api/auth/login/", {"username": "session-user", "password": "pass12345"}),
        (
            "/api/auth/initial-password/",
            {
                "username": "session-user",
                "current_password": "Temporary-pass-2026!",
                "new_password": "Fresh-portal-pass-2026!",
            },
        ),
        (
            "/api/portal/register/",
            {
                "username": "cross-site",
                "password": "secret12345",
                "first_name": "A",
                "phone": "+77001112233",
            },
        ),
        ("/api/auth/refresh/", {}),
        ("/api/auth/logout/", {}),
    ],
    ids=["login", "initial-password", "register", "refresh", "logout"],
)
def test_session_endpoints_require_the_sites_own_origin(user, url, data, origin):
    raw = str(SessionRefreshToken.for_user(user))
    client = APIClient() if origin is None else APIClient(HTTP_ORIGIN=origin)
    client.cookies[REFRESH_COOKIE] = raw

    response = client.post(url, data, format="json")

    assert response.status_code == 403
    assert response.data["code"] == "bad_origin"
    assert REFRESH_COOKIE not in response.cookies  # 403 куку не трогает
    assert not BlacklistedToken.objects.exists()
    assert not type(user).objects.filter(username="cross-site").exists()


@override_settings(AUTH_TRUSTED_ORIGINS=["http://localhost:3000"])
def test_trusted_dev_origin_can_log_in(user):
    client = APIClient(HTTP_ORIGIN="http://localhost:3000")

    response = client.post(
        "/api/auth/login/",
        {"username": user.username, "password": "pass12345"},
        format="json",
    )

    assert response.status_code == 200
    assert_session_started(response, user)


@override_settings(AUTH_REFRESH_COOKIE_SECURE=True)
def test_refresh_cookie_is_secure_when_the_settings_say_so(api_client, user):
    login = api_client.post(
        "/api/auth/login/",
        {"username": user.username, "password": "pass12345"},
        format="json",
    )
    assert login.cookies[REFRESH_COOKIE]["secure"] is True
    assert _refresh(api_client).cookies[REFRESH_COOKIE]["secure"] is True
    assert _logout(api_client).cookies[REFRESH_COOKIE]["secure"] is True


def test_entrypoint_flushes_expired_tokens_after_migrate():
    script = (Path(__file__).resolve().parents[3] / "entrypoint.sh").read_text()
    migrate = script.index("python manage.py migrate --noinput")
    flush = script.index("python manage.py flushexpiredtokens\n")
    assert migrate < flush < script.index('exec "$@"')
