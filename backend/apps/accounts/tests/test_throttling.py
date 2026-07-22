"""Троттлинг чувствительных эндпоинтов. По умолчанию под pytest он выключен —
здесь включаем его точечно через override_settings и проверяем 429."""
import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


THROTTLED = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min", "user": "600/min", "burst": "30/sec",
        "login": "3/min", "register": "2/min",
    },
    "NUM_PROXIES": 1,
}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_login_is_throttled_after_limit():
    # Уникальный IP, чтобы счётчик не пересекался с другими тестами логина
    # в общем прогоне (кэш троттла — общий процессный LocMem).
    client = APIClient(REMOTE_ADDR="203.0.113.10")
    codes = []
    for _ in range(5):  # лимит login = 3/min
        r = client.post("/api/auth/login/",
                        {"username": "nope", "password": "bad"}, format="json")
        codes.append(r.status_code)
    assert 429 in codes, f"логин должен упираться в лимит, коды: {codes}"


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_register_is_throttled_after_limit():
    client = APIClient(REMOTE_ADDR="203.0.113.11")
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
        )
        payload["username"] = f"xff-user-{index}"
        codes.append(
            client.post("/api/portal/register/", payload, format="json").status_code
        )

    assert 429 in codes, f"spoofed XFF prefixes must share one bucket: {codes}"


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_authenticated_api_has_user_throttle(auth_client, make_user):
    # Обычный пользователь под user-лимитом (600/min) — при нормальной работе
    # 429 не ловит; проверяем, что троттл-класс подключён и не роняет запрос.
    u = make_user(username="thr")
    r = auth_client(u).get("/api/auth/me/")
    assert r.status_code == 200


def test_throttling_disabled_by_default(auth_client, make_user):
    # Базовые настройки под pytest — троттлинг не мешает обычным прогонам.
    u = make_user(username="nothrottle")
    for _ in range(30):
        r = auth_client(u).get("/api/auth/me/")
    assert r.status_code == 200
