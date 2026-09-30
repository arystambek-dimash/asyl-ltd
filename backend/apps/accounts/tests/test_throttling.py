"""Троттлинг чувствительных эндпоинтов. По умолчанию под pytest он выключен —
здесь включаем его точечно через override_settings и проверяем 429."""
import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.credentials import REFRESH_COOKIE, SessionRefreshToken
from apps.accounts.views import LogoutView, RevocableTokenRefreshView
from config.throttles import TokenRefreshRateThrottle

pytestmark = pytest.mark.django_db


THROTTLED = {
    **settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        "login": "3/min",
        "register": "2/min",
        "token_refresh": "2/min",
    },
    "NUM_PROXIES": 1,
}
ORIGIN = "http://testserver"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_login_is_throttled_after_limit():
    # Уникальный IP, чтобы счётчик не пересекался с другими тестами логина
    # в общем прогоне (кэш троттла — общий процессный LocMem).
    client = APIClient(REMOTE_ADDR="203.0.113.10", HTTP_ORIGIN=ORIGIN)
    codes = []
    for _ in range(5):  # лимит login = 3/min
        r = client.post("/api/auth/login/",
                        {"username": "nope", "password": "bad"}, format="json")
        codes.append(r.status_code)
    assert 429 in codes, f"логин должен упираться в лимит, коды: {codes}"


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_register_is_throttled_after_limit():
    client = APIClient(REMOTE_ADDR="203.0.113.11", HTTP_ORIGIN=ORIGIN)
    codes = []
    for i in range(4):  # лимит register = 2/min
        r = client.post("/api/portal/register/", {
            "username": f"u{i}", "password": "password123",
            "first_name": "A", "last_name": "B", "phone": "+7700",
        }, format="json")
        codes.append(r.status_code)
    assert 429 in codes, f"регистрация должна упираться в лимит, коды: {codes}"


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_registration_throttle_ignores_client_supplied_xff_prefix():
    """One trusted nginx hop means spoofing the first XFF value cannot rotate IPs."""
    payload = {
        "username": "xff-user",
        "password": "password123",
        "first_name": "A",
        "last_name": "B",
        "company_name": "Company",
        "phone": "+7700",
        "iin": "123456789012",
    }
    codes = []
    for index in range(4):
        client = APIClient(
            REMOTE_ADDR="10.0.0.10",
            HTTP_X_FORWARDED_FOR=f"198.51.100.{index}, 203.0.113.12",
            HTTP_ORIGIN=ORIGIN,
        )
        payload["username"] = f"xff-user-{index}"
        codes.append(
            client.post("/api/portal/register/", payload, format="json").status_code
        )

    assert 429 in codes, f"spoofed XFF prefixes must share one bucket: {codes}"


def test_refresh_and_logout_share_their_own_per_ip_scope():
    assert RevocableTokenRefreshView.throttle_classes == [TokenRefreshRateThrottle]
    assert LogoutView.throttle_classes == [TokenRefreshRateThrottle]
    assert TokenRefreshRateThrottle.scope == "token_refresh"


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_refresh_is_throttled_without_ending_the_session(make_user):
    """429 — не конец сессии: кука остаётся, фронт просто повторит позже."""
    client = APIClient(REMOTE_ADDR="203.0.113.13", HTTP_ORIGIN=ORIGIN)
    client.cookies[REFRESH_COOKIE] = str(SessionRefreshToken.for_user(make_user()))

    refreshes = [client.post("/api/auth/refresh/", {}, format="json") for _ in range(3)]
    logout = client.post("/api/auth/logout/", {}, format="json")

    assert [response.status_code for response in refreshes] == [200, 200, 429]
    assert REFRESH_COOKIE not in refreshes[-1].cookies
    assert logout.status_code == 429  # тот же лимит, что у refresh
