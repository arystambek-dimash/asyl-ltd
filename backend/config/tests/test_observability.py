import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from config import observability

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_sensitive_key_scrubbing_is_recursive_and_normalizes_headers():
    source = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret",
                "X-Request-ID": "safe-id",
                "Set-Cookie": "session=secret",
                "X-Api-Key": "service-secret",
                "X-Webhook-Signature": "sha256=secret",
            },
            "data": [
                {"password": "hidden", "name": "safe"},
                ("safe", {"api-key": "hidden"}),
                ["HTTP_X_API_KEY", "hidden"],
                {
                    "password_confirmation": "hidden",
                    "client_secret": "hidden",
                    "privateKey": "hidden",
                    "apiToken": "hidden",
                    "auth_credentials": "hidden",
                    "mysql_pwd": "hidden",
                    "sessionId": "hidden",
                    "xForwardedFor": "203.0.113.10",
                    "xCsrfToken": "hidden",
                    "XSRF-TOKEN": "hidden",
                    "_csrf": "hidden",
                    "_xsrf": "hidden",
                    "PHPSESSID": "hidden",
                    "connect.sid": "hidden",
                    "aiohttp_session": "hidden",
                    "token_count": 12,
                    "public_key": "safe-public-key",
                },
            ],
        }
    }

    scrubbed = observability.scrub_sensitive_data(source)

    assert scrubbed["request"]["headers"] == {
        "Authorization": observability.REDACTED,
        "X-Request-ID": "safe-id",
        "Set-Cookie": observability.REDACTED,
        "X-Api-Key": observability.REDACTED,
        "X-Webhook-Signature": observability.REDACTED,
    }
    assert scrubbed["request"]["data"][0] == {
        "password": observability.REDACTED,
        "name": "safe",
    }
    assert scrubbed["request"]["data"][1][1]["api-key"] == observability.REDACTED
    assert scrubbed["request"]["data"][2] == [
        "HTTP_X_API_KEY",
        observability.REDACTED,
    ]
    assert scrubbed["request"]["data"][3] == {
        "password_confirmation": observability.REDACTED,
        "client_secret": observability.REDACTED,
        "privateKey": observability.REDACTED,
        "apiToken": observability.REDACTED,
        "auth_credentials": observability.REDACTED,
        "mysql_pwd": observability.REDACTED,
        "sessionId": observability.REDACTED,
        "xForwardedFor": observability.REDACTED,
        "xCsrfToken": observability.REDACTED,
        "XSRF-TOKEN": observability.REDACTED,
        "_csrf": observability.REDACTED,
        "_xsrf": observability.REDACTED,
        "PHPSESSID": observability.REDACTED,
        "connect.sid": observability.REDACTED,
        "aiohttp_session": observability.REDACTED,
        "token_count": 12,
        "public_key": "safe-public-key",
    }
    assert source["request"]["headers"]["Authorization"] == "Bearer secret"


def test_sentry_is_not_initialized_without_a_dsn(monkeypatch):
    initialize = Mock()
    monkeypatch.setattr(observability.sentry_sdk, "init", initialize)

    initialized = observability.initialize_sentry(
        dsn="  ",
        release="abc",
        environment="production",
        service="backend",
    )

    assert initialized is False
    initialize.assert_not_called()


def test_base_settings_default_to_production_environment_before_sentry_init():
    environment = os.environ.copy()
    environment.pop("APP_ENVIRONMENT", None)
    environment.pop("SENTRY_BACKEND_DSN", None)
    environment["DEBUG"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config._settings.base import APP_ENVIRONMENT; print(APP_ENVIRONMENT)",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "production"


def test_production_settings_establish_environment_before_sentry_init():
    environment = os.environ.copy()
    environment.pop("APP_ENVIRONMENT", None)
    environment.pop("DEBUG", None)
    environment.pop("SENTRY_BACKEND_DSN", None)
    environment.update(
        {
            "SECRET_KEY": "production-secret-with-more-than-fifty-distinct-ish-characters-123",
            "DB_PASSWORD": "test-database-password",
            "REDIS_URL": "redis://localhost:6379/0",
            "ALLOWED_HOSTS": "example.test",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from config._settings.production import APP_ENVIRONMENT; "
                "print(APP_ENVIRONMENT)"
            ),
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "production"


def test_sentry_configuration_is_private_opt_in_and_tagged(monkeypatch):
    initialize = Mock()
    set_tag = Mock()
    monkeypatch.setattr(observability.sentry_sdk, "init", initialize)
    monkeypatch.setattr(observability.sentry_sdk, "set_tag", set_tag)

    initialized = observability.initialize_sentry(
        dsn=" https://public@example.invalid/1 ",
        release="deadbeef",
        environment="production",
        service="payment-monitor",
        enable_logs=True,
    )

    assert initialized is True
    options = initialize.call_args.kwargs
    assert options["dsn"] == "https://public@example.invalid/1"
    assert options["release"] == "deadbeef"
    assert options["environment"] == "production"
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["traces_sample_rate"] == 0
    assert options["profiles_sample_rate"] == 0
    assert options["enable_logs"] is True
    scrubbed = options["before_send"](
        {
            "tags": {},
            "request": {
                "url": (
                    "https://example.test/api/orders/"
                    "?search=customer-phone#access_token"
                ),
                "query_string": "search=customer-phone",
                "cookies": "session=secret",
                "headers": {
                    "Referer": (
                        "https://example.test/orders?search=customer-phone#access_token"
                    ),
                },
                "data": {
                    "refresh_token": "hidden",
                    "customer_phone": "+77001234567",
                },
                "body": "customer_phone=+77001234567",
                "form_data": {"customer_phone": "+77001234567"},
                "files": [{"name": "private.pdf"}],
            },
            "response": {
                "status_code": 422,
                "headers": {"Set-Cookie": "session=secret"},
                "cookies": {"session": "secret"},
                "data": {"customer_phone": "+77001234567"},
                "body": "customer_phone=+77001234567",
            },
            "breadcrumbs": {
                "values": [
                    {
                        "data": {
                            "url": "/api/orders/?search=customer-phone",
                        }
                    }
                ]
            },
        },
        {},
    )
    assert scrubbed["tags"]["service"] == "payment-monitor"
    assert scrubbed["request"]["url"] == "https://example.test/api/orders/"
    assert "query_string" not in scrubbed["request"]
    assert "headers" not in scrubbed["request"]
    assert "cookies" not in scrubbed["request"]
    assert "data" not in scrubbed["request"]
    assert "body" not in scrubbed["request"]
    assert "form_data" not in scrubbed["request"]
    assert "files" not in scrubbed["request"]
    assert scrubbed["response"] == {"status_code": 422}
    assert scrubbed["breadcrumbs"]["values"][0]["data"]["url"] == "/api/orders/"
    assert "customer-phone" not in json.dumps(scrubbed)
    assert "access_token" not in json.dumps(scrubbed)
    assert "Referer" not in json.dumps(scrubbed)
    assert "+77001234567" not in json.dumps(scrubbed)
    assert options["before_send_transaction"] is not None
    assert options["_experiments"]["data_collection"] == (
        observability._DATA_COLLECTION_POLICY
    )
    assert options["_experiments"]["data_collection"]["frame_context_lines"] == 0
    scrubbed_log = options["before_send_log"](
        {
            "body": "safe",
            "attributes": {
                "X-Api-Key": "hidden",
                "request": {
                    "url": "/api/orders?search=customer-phone#access_token",
                    "query_string": "search=customer-phone",
                    "cookies": "session=secret",
                    "data": {"customer_phone": "+77001234567"},
                    "body": "customer_phone=+77001234567",
                    "form_data": {"customer_phone": "+77001234567"},
                    "files": [{"name": "private.pdf"}],
                    "headers": {
                        "Referer": (
                            "https://example.test/orders"
                            "?search=customer-phone#access_token"
                        ),
                    },
                },
                "response": {
                    "status_code": 422,
                    "headers": {"Set-Cookie": "session=secret"},
                    "cookies": {"session": "secret"},
                    "data": {"customer_phone": "+77001234567"},
                    "body": "customer_phone=+77001234567",
                },
                "callback_url": (
                    "https://example.test/done?search=customer-phone#access_token"
                ),
            },
        },
        {},
    )
    assert scrubbed_log["attributes"] == {
        "X-Api-Key": observability.REDACTED,
        "request": {"url": "/api/orders"},
        "response": {"status_code": 422},
        "callback_url": "https://example.test/done",
        "service": "payment-monitor",
        "release": "deadbeef",
        "environment": "production",
    }
    assert "customer-phone" not in json.dumps(scrubbed_log)
    assert "access_token" not in json.dumps(scrubbed_log)
    assert "Referer" not in json.dumps(scrubbed_log)
    assert "+77001234567" not in json.dumps(scrubbed_log)
    set_tag.assert_called_once_with("service", "payment-monitor")


def test_transaction_callback_scrubs_nested_spans_without_streaming(monkeypatch):
    initialize = Mock()
    monkeypatch.setattr(observability.sentry_sdk, "init", initialize)
    monkeypatch.setattr(observability.sentry_sdk, "set_tag", Mock())

    observability.initialize_sentry(
        dsn="https://public@example.invalid/1",
        release="deadbeef",
        environment="production",
        service="backend",
    )

    options = initialize.call_args.kwargs
    assert options["traces_sample_rate"] == 0
    assert "trace_lifecycle" not in options
    assert "before_send_span" not in options

    scrubbed = options["before_send_transaction"](
        {
            "type": "transaction",
            "transaction": "GET /api/orders",
            "request": {
                "url": (
                    "https://example.test/api/orders?search=customer-phone#access_token"
                ),
                "headers": {"Referer": "https://example.test/private?customer-phone"},
                "data": {"customer_phone": "+77001234567"},
            },
            "spans": [
                {
                    "description": ("/api/orders?search=customer-phone#access_token"),
                    "data": {
                        "http.url": (
                            "https://example.test/api/orders"
                            "?search=customer-phone#access_token"
                        ),
                        "httpQuery": "search=customer-phone",
                        "http.fragment": "access_token",
                        "url_query": "search=customer-phone",
                        "urlFragment": "access_token",
                        "http.request.body": "customer_phone=+77001234567",
                        "http.request.body.size": 32,
                        "httpRequestHeaders": "Referer: private",
                        "http.response.data": "customer_phone=+77001234567",
                        "http.response.body.size": 64,
                        "httpResponseCookies": "session=secret",
                        "http.request.method": "GET",
                        "http.response.status_code": 200,
                        "token_count": 4,
                        "public_key": "safe-public-key",
                    },
                }
            ],
        },
        {},
    )

    assert scrubbed["tags"]["service"] == "backend"
    assert scrubbed["request"] == {"url": "https://example.test/api/orders"}
    assert scrubbed["spans"][0]["description"] == "/api/orders"
    assert scrubbed["spans"][0]["data"] == {
        "http.url": "https://example.test/api/orders",
        "http.request.method": "GET",
        "http.response.status_code": 200,
        "token_count": 4,
        "public_key": "safe-public-key",
    }
    serialized = json.dumps(scrubbed)
    assert "customer-phone" not in serialized
    assert "access_token" not in serialized
    assert "+77001234567" not in serialized
    assert "Referer" not in serialized


def test_gunicorn_disables_raw_access_targets_but_keeps_error_log() -> None:
    dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "--access-logfile" not in dockerfile
    assert '"--error-logfile", "-"' in dockerfile


def test_json_formatter_has_stable_context_and_exception_fields():
    formatter = observability.JsonLogFormatter(
        service="camera-monitor",
        environment="production",
        release="deadbeef",
    )
    try:
        raise RuntimeError("probe failed")
    except RuntimeError:
        record = logging.LogRecord(
            name="apps.cameras.health",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="camera %s failed",
            args=("cam1",),
            exc_info=__import__("sys").exc_info(),
        )

    payload = json.loads(formatter.format(record))

    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "ERROR"
    assert payload["logger"] == "apps.cameras.health"
    assert payload["message"] == "camera cam1 failed"
    assert "RuntimeError: probe failed" in payload["exception"]
    assert payload["service"] == "camera-monitor"
    assert payload["environment"] == "production"
    assert payload["release"] == "deadbeef"


@pytest.mark.parametrize("value", ["-0.1", "1.1", "not-a-number"])
def test_sample_rate_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="SENTRY_RATE"):
        observability.sample_rate_from_env(value, name="SENTRY_RATE")


def test_logging_config_keeps_local_output_readable_and_production_json():
    common = {
        "level": "INFO",
        "service": "backend",
        "environment": "production",
        "release": "abc",
    }

    readable = observability.build_logging_config(log_format="readable", **common)
    structured = observability.build_logging_config(log_format="json", **common)

    assert readable["handlers"]["console"]["formatter"] == "readable"
    assert structured["handlers"]["console"]["formatter"] == "json"
