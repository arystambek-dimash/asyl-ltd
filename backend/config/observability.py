"""Small, opt-in observability primitives shared by Django settings.

Sentry is intentionally initialized only when a DSN is configured.  The JSON
formatter writes to stdout; Docker remains responsible for collection and
rotation, while a remote sink can be added independently.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

REDACTED = "[Filtered]"

_SENSITIVE_KEYS = frozenset(
    {
        "_csrf",
        "_csrf_token",
        "_session",
        "_xsrf",
        "access_token",
        "aiohttp_session",
        "ai_service_api_key",
        "apikey",
        "api_key",
        "apipay_api_key",
        "apipay_webhook_secret",
        "authorization",
        "auth",
        "camera_pass",
        "conveyor_ai_callback_token_sha256",
        "connect.sid",
        "cookie",
        "credentials",
        "csrf",
        "csrftoken",
        "csrf_token",
        "csrfmiddlewaretoken",
        "ip_address",
        "mysql_pwd",
        "passwd",
        "password",
        "privatekey",
        "private_key",
        "proxy_authorization",
        "phpsessid",
        "remote_addr",
        "refresh",
        "refresh_token",
        "secret",
        "secret_key",
        "session",
        "sessionid",
        "session_id",
        "sentry_auth_token",
        "set_cookie",
        "symfony",
        "token",
        "user_session",
        "webhook_signature",
        "http_x_api_key",
        "http_x_webhook_signature",
        "x_api_key",
        "x_csrftoken",
        "x_csrf_token",
        "x_forwarded_for",
        "x_real_ip",
        "x_webhook_signature",
        "xsrf_token",
    }
)

_SENSITIVE_KEY_PREFIXES = (
    "password_",
    "passwd_",
    "secret_",
    "credential_",
    "credentials_",
)
_SENSITIVE_KEY_SUFFIXES = (
    "_password",
    "_passwd",
    "_secret",
    "_credential",
    "_credentials",
    "_token",
    "_api_key",
    "_private_key",
    "_authorization",
    "_webhook_signature",
)

_DATA_COLLECTION_POLICY = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {"request": {"mode": "off"}},
    "http_bodies": [],
    "url_query_params": {"mode": "off"},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,
    "frame_context_lines": 0,
}


def _normalized_key(value: object) -> str:
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value).strip())
    return key.lower().replace("-", "_")


def _is_sensitive_key(value: object) -> bool:
    normalized = _normalized_key(value)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.startswith(_SENSITIVE_KEY_PREFIXES)
        or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)
    )


def scrub_sensitive_data(value: Any) -> Any:
    """Return a recursively scrubbed copy of Sentry-compatible data.

    Sentry events are plain mappings/sequences.  Unknown scalar and object
    values are left alone so the SDK can serialize them using its normal rules.
    Tuple/list shape is retained because breadcrumbs and stack data sometimes
    use tuples before the SDK normalizes the event.
    """

    if isinstance(value, Mapping):
        return {
            key: (REDACTED if _is_sensitive_key(key) else scrub_sensitive_data(item))
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        if (
            len(value) == 2
            and isinstance(value[0], str)
            and _is_sensitive_key(value[0])
        ):
            scrubbed = (value[0], REDACTED)
        else:
            scrubbed = tuple(scrub_sensitive_data(item) for item in value)
        return scrubbed if isinstance(value, tuple) else list(scrubbed)
    return value


def _without_url_private_parts(value: str) -> str:
    if not (
        value.startswith(("http://", "https://", "/"))
        and ("?" in value or "#" in value)
    ):
        return value
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _strip_url_queries(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _strip_url_queries(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        sanitized = tuple(_strip_url_queries(item) for item in value)
        return sanitized if isinstance(value, tuple) else list(sanitized)
    if isinstance(value, str):
        return _without_url_private_parts(value)
    return value


_PRIVATE_REQUEST_KEYS = frozenset(
    {
        "headers",
        "cookies",
        "query_string",
        "data",
        "body",
        "form_data",
        "files",
    }
)
_PRIVATE_RESPONSE_KEYS = frozenset({"headers", "cookies", "data", "body"})
_PRIVATE_TRACE_ATTRIBUTE_KEYS = frozenset(
    {
        "http_fragment",
        "http_query",
        "http_query_params",
        "http_query_string",
        "url_fragment",
        "url_query",
        "url_query_params",
        "url_query_string",
    }
)
_PRIVATE_TRACE_REQUEST_FIELDS = (
    "header",
    "headers",
    "cookie",
    "cookies",
    "query",
    "fragment",
    "data",
    "body",
    "form_data",
    "file",
    "files",
    "payload",
)
_PRIVATE_TRACE_RESPONSE_FIELDS = (
    "header",
    "headers",
    "cookie",
    "cookies",
    "data",
    "body",
    "form_data",
    "file",
    "files",
    "payload",
)


def _strip_http_metadata(
    value: Any,
    *,
    inside_request: bool = False,
    inside_response: bool = False,
) -> Any:
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            if inside_request and normalized in _PRIVATE_REQUEST_KEYS:
                continue
            if inside_response and normalized in _PRIVATE_RESPONSE_KEYS:
                continue
            sanitized[key] = _strip_http_metadata(
                item,
                inside_request=inside_request or normalized == "request",
                inside_response=inside_response or normalized == "response",
            )
        return sanitized
    if isinstance(value, (tuple, list)):
        sanitized_items = tuple(
            _strip_http_metadata(
                item,
                inside_request=inside_request,
                inside_response=inside_response,
            )
            for item in value
        )
        return sanitized_items if isinstance(value, tuple) else list(sanitized_items)
    return value


def _normalized_trace_attribute_key(value: object) -> str:
    normalized = re.sub(r"[./:\s]+", "_", _normalized_key(value))
    return re.sub(r"_+", "_", normalized)


def _has_private_trace_field(
    normalized: str,
    *,
    scope: str,
    fields: tuple[str, ...],
) -> bool:
    for prefix in (f"{scope}_", f"http_{scope}_"):
        if not normalized.startswith(prefix):
            continue
        field = normalized[len(prefix) :]
        if any(
            field == private_field or field.startswith(f"{private_field}_")
            for private_field in fields
        ):
            return True
    return False


def _is_private_trace_attribute_key(value: object) -> bool:
    normalized = _normalized_trace_attribute_key(value)
    return (
        normalized in _PRIVATE_TRACE_ATTRIBUTE_KEYS
        or _has_private_trace_field(
            normalized,
            scope="request",
            fields=_PRIVATE_TRACE_REQUEST_FIELDS,
        )
        or _has_private_trace_field(
            normalized,
            scope="response",
            fields=_PRIVATE_TRACE_RESPONSE_FIELDS,
        )
    )


def _strip_private_trace_attributes(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _strip_private_trace_attributes(item)
            for key, item in value.items()
            if not _is_private_trace_attribute_key(key)
        }
    if isinstance(value, (tuple, list)):
        sanitized = tuple(_strip_private_trace_attributes(item) for item in value)
        return sanitized if isinstance(value, tuple) else list(sanitized)
    return value


def scrub_event(event: dict[str, Any]) -> dict[str, Any]:
    """Apply a fail-closed request policy in addition to SDK collection flags."""

    scrubbed = scrub_sensitive_data(event)
    # SDK PII defaults use a deny-list and may otherwise retain Referer and
    # ordinary query parameters such as customer-name/phone searches. Logs can
    # place their request object below attributes, so apply this recursively.
    return _strip_private_trace_attributes(
        _strip_url_queries(_strip_http_metadata(scrubbed))
    )


def _before_send(service: str):
    def scrub_sentry_event(
        event: dict[str, Any], hint: dict[str, Any]
    ) -> dict[str, Any]:
        del hint
        event.setdefault("tags", {}).setdefault("service", service)
        return scrub_event(event)

    return scrub_sentry_event


def _before_send_log(service: str, release: str, environment: str):
    def scrub_log(log: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
        del hint
        log.setdefault("attributes", {}).update(
            {
                "service": service,
                "release": release,
                "environment": environment,
            }
        )
        return scrub_event(log)

    return scrub_log


def initialize_sentry(
    *,
    dsn: str,
    release: str,
    environment: str,
    service: str,
    enable_logs: bool = False,
    traces_sample_rate: float = 0.0,
    profiles_sample_rate: float = 0.0,
) -> bool:
    """Initialize the Django SDK when explicitly configured.

    Returning whether initialization occurred makes the opt-in contract easy to
    assert without relying on Sentry's process-global client state.
    """

    if not dsn.strip():
        return False

    sentry_sdk.init(
        dsn=dsn.strip(),
        integrations=[DjangoIntegration()],
        release=release,
        environment=environment,
        send_default_pii=False,
        # Frame locals can contain secrets under arbitrary business-specific
        # names that an exact-key scrubber cannot reliably recognize.
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        enable_logs=enable_logs,
        before_send=_before_send(service),
        # In this SDK before_send_span requires trace_lifecycle="stream".
        # Keep the default transaction-envelope lifecycle; the transaction
        # callback recursively scrubs its nested spans before they are sent.
        before_send_transaction=_before_send(service),
        before_send_log=_before_send_log(service, release, environment),
        _experiments={"data_collection": _DATA_COLLECTION_POLICY},
    )
    sentry_sdk.set_tag("service", service)
    return True


class JsonLogFormatter(logging.Formatter):
    """Emit the stable fields required by the production stdout contract."""

    def __init__(self, *, service: str, environment: str, release: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment
        self.release = release

    def format(self, record: logging.LogRecord) -> str:
        exception = None
        if record.exc_info:
            exception = self.formatException(record.exc_info)
        elif record.exc_text:
            exception = record.exc_text

        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "exception": exception,
            "service": self.service,
            "environment": self.environment,
            "release": self.release,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_logging_config(
    *,
    log_format: str,
    level: str,
    service: str,
    environment: str,
    release: str,
) -> dict[str, Any]:
    """Build a Django dictConfig with readable or JSON stdout output."""

    use_json = log_format.strip().lower() == "json"
    formatter = "json" if use_json else "readable"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "readable": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "json": {
                "()": "config.observability.JsonLogFormatter",
                "service": service,
                "environment": environment,
                "release": release,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": formatter,
            }
        },
        "root": {"handlers": ["console"], "level": level},
        # Django otherwise installs a second formatter/handler for its own
        # records.  Propagation keeps one stable stdout shape instead.
        "loggers": {
            "django": {"handlers": [], "level": level, "propagate": True},
        },
    }


def sample_rate_from_env(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number between 0 and 1") from exc
    if not 0 <= parsed <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return parsed


def env_flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
