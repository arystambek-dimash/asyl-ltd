"""Настройки сессии входа по окружениям: сроки токенов, Secure-кука, origin'ы, лимит."""
import json
import os
from datetime import timedelta

from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.credentials import SessionRefreshToken
from config.tests.settings_process import import_settings

LOCAL_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
SITE_ORIGINS = ["https://asyl-ltd.kz", "https://www.asyl-ltd.kz"]
PRODUCTION_ENV = {
    "DEBUG": "0",
    "SECRET_KEY": "settings-test-" + "abcdefghij" * 5,
    "DB_PASSWORD": "db-password",
    "REDIS_URL": "redis://redis:6379/0",
    "ALLOWED_HOSTS": "asyl-ltd.kz,www.asyl-ltd.kz",
    "CORS_ALLOWED_ORIGINS": ",".join(SITE_ORIGINS),
}


def _read(module, *names, **env):
    result = import_settings(
        {"PATH": os.environ.get("PATH", ""), **env}, *names, module=module
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_access_lives_fifteen_minutes_and_the_session_thirty_days():
    assert AccessToken.lifetime == timedelta(minutes=15)
    assert SessionRefreshToken.lifetime == timedelta(days=30)


def test_production_refresh_cookie_is_secure_and_trusts_site_and_dev_origins():
    secure, origins = _read(
        "production", "AUTH_REFRESH_COOKIE_SECURE", "AUTH_TRUSTED_ORIGINS",
        **PRODUCTION_ENV,
    )

    assert secure is True
    assert origins == LOCAL_ORIGINS + SITE_ORIGINS


def test_local_refresh_cookie_works_over_http_for_the_dev_server():
    secure, origins = _read(
        "local", "AUTH_REFRESH_COOKIE_SECURE", "AUTH_TRUSTED_ORIGINS", DEBUG="1"
    )

    assert secure is False
    assert origins == LOCAL_ORIGINS


def test_token_refresh_rate_defaults_to_240_per_minute_and_follows_env():
    (default,) = _read("base", "REST_FRAMEWORK")
    (tuned,) = _read("base", "REST_FRAMEWORK", THROTTLE_TOKEN_REFRESH="100/min")

    assert default["DEFAULT_THROTTLE_RATES"]["token_refresh"] == "240/min"
    assert tuned["DEFAULT_THROTTLE_RATES"]["token_refresh"] == "100/min"
