"""Strict HTTP client for the Grain site's physical truck scale."""

from __future__ import annotations

import http.client
import json
import logging
import math
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from threading import Lock
from time import monotonic
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache, caches
from django.db import DatabaseError, connection
from rest_framework.exceptions import APIException

MAX_RESPONSE_BYTES = 32 * 1024
WEIGHT_QUANTUM = Decimal("0.01")

# The Grain screen supports two independent hardware slots: railway wagons
# arrive with grain, while trucks collect outgoing cargo. A slot may be empty
# until its scale is installed; configuration never falls back across slots.
WAGON_SCALE_KEY = "wagon"
TRUCK_SCALE_KEY = "truck"
# The legacy singular `/truck-scale/reading/` endpoint remains a truck alias.
# New callers should always choose one of the explicit plural scale routes.
DEFAULT_SCALE_KEY = TRUCK_SCALE_KEY
SCALE_KEYS = frozenset({WAGON_SCALE_KEY, TRUCK_SCALE_KEY})

CAPTURE_LOCK_PREFIX = "grain:authoritative-scale-capture:v1"
# Outlive the 60-second Gunicorn request ceiling plus cleanup/release grace.
CAPTURE_LOCK_MIN_SECONDS = 90
CAPTURE_LOCK_MARGIN_SECONDS = 15
CAPTURE_DB_TIMEOUT_MAX_SECONDS = 5
CAPTURE_DB_TIMEOUT_GRACE_SECONDS = 5
CAPTURE_ADVISORY_NAMESPACE = 0x4153594C
_CAPTURE_ADVISORY_IDS = {
    WAGON_SCALE_KEY: 1,
    TRUCK_SCALE_KEY: 2,
}
_CAPTURE_LEASE_DEADLINE: ContextVar[float | None] = ContextVar(
    "grain_scale_capture_lease_deadline",
    default=None,
)
_LOCAL_CAPTURE_LOCK_GUARD = Lock()
_COMPARE_AND_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
log = logging.getLogger(__name__)

_URL_SETTING_BY_SCALE = {
    WAGON_SCALE_KEY: "WAGON_SCALE_API_URL",
    TRUCK_SCALE_KEY: "TRUCK_SCALE_API_URL",
}


class TruckScaleDisabled(APIException):
    status_code = 503
    default_detail = "Весы не настроены."
    default_code = "truck_scale_disabled"


class TruckScaleUnavailable(APIException):
    status_code = 503
    default_detail = "Весы сейчас недоступны."
    default_code = "truck_scale_unreachable"


class TruckScaleNotReady(APIException):
    status_code = 409
    default_detail = "Весы ещё не готовы зафиксировать вес."
    default_code = "truck_scale_not_ready"


class TruckScaleCaptureBusy(APIException):
    status_code = 409
    default_detail = "Весы уже фиксируют другое взвешивание."
    default_code = "truck_scale_capture_busy"


class TruckScaleApplyUnavailable(APIException):
    status_code = 503
    default_detail = "Не удалось безопасно сохранить показание весов."
    default_code = "truck_scale_apply_unavailable"


class TruckScaleMalformedResponse(APIException):
    status_code = 502
    default_detail = "Весы вернули некорректный ответ."
    default_code = "truck_scale_malformed_response"


@dataclass(frozen=True, slots=True)
class ScaleReading:
    weight_kg: Decimal
    age_seconds: Decimal
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class ScaleObservation:
    """Sanitized read-only state for an operator display.

    Unlike ``ScaleReading``, an observation may describe an empty or moving
    scale. It is never accepted as the value of a business operation: capture
    commands always call ``read_truck_scale`` again.
    """

    state: str
    weight_kg: Decimal | None
    connected: bool
    stable: bool
    stale: bool
    age_seconds: Decimal | None
    updated_at: str | None


def authoritative_capture_lock_key(scale_key: str) -> str:
    if scale_key not in SCALE_KEYS:
        raise ValueError(f"Unknown truck scale: {scale_key}")
    return f"{CAPTURE_LOCK_PREFIX}:{scale_key}"


def _capture_lock_seconds() -> int:
    try:
        timeout = math.ceil(float(settings.TRUCK_SCALE_TIMEOUT_SECONDS))
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise TruckScaleUnavailable() from exc
    if timeout <= 0:
        raise TruckScaleUnavailable()
    return max(CAPTURE_LOCK_MIN_SECONDS, timeout + CAPTURE_LOCK_MARGIN_SECONDS)


def authoritative_db_timeout_ms() -> int:
    """Bound each PostgreSQL apply statement inside the remaining lease."""

    try:
        scale_timeout = math.ceil(float(settings.TRUCK_SCALE_TIMEOUT_SECONDS))
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise TruckScaleUnavailable() from exc
    remaining_seconds = _capture_lock_seconds() - scale_timeout
    lease_deadline = _CAPTURE_LEASE_DEADLINE.get()
    if lease_deadline is not None:
        remaining_seconds = min(
            remaining_seconds,
            lease_deadline - monotonic(),
        )
    budget_seconds = min(
        CAPTURE_DB_TIMEOUT_MAX_SECONDS,
        remaining_seconds - CAPTURE_DB_TIMEOUT_GRACE_SECONDS,
    )
    if budget_seconds <= 0:
        raise TruckScaleApplyUnavailable("Истёк безопасный срок сохранения веса.")
    return max(1, math.floor(budget_seconds * 1000))


def configure_authoritative_db_timeouts() -> None:
    """Apply transaction-local PostgreSQL limits before any blocking write."""

    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise DatabaseError("Authoritative DB timeouts require an atomic block")
    timeout_ms = authoritative_db_timeout_ms()
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL lock_timeout = '{timeout_ms}ms'")
        cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")


def _claim_database_capture(scale_key: str) -> bool:
    """Try a PostgreSQL session lock that survives Redis lease expiry."""

    if connection.vendor != "postgresql":
        return False
    advisory_id = _CAPTURE_ADVISORY_IDS[scale_key]
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                [CAPTURE_ADVISORY_NAMESPACE, advisory_id],
            )
            row = cursor.fetchone()
    except DatabaseError as exc:
        connection.close()
        raise TruckScaleUnavailable(
            "Не удалось заблокировать весы в базе данных."
        ) from exc
    if row != (True,):
        raise TruckScaleCaptureBusy()
    return True


def _release_database_capture(scale_key: str) -> None:
    advisory_id = _CAPTURE_ADVISORY_IDS[scale_key]
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s, %s)",
                [CAPTURE_ADVISORY_NAMESPACE, advisory_id],
            )
            row = cursor.fetchone()
        if row != (True,):
            log.error("PostgreSQL scale advisory lock was not owned at release")
    except DatabaseError:
        # Closing the exact session is the fail-closed release for a
        # session-level advisory lock. PostgreSQL also releases it on worker
        # death, independently of the finite Redis lease.
        log.exception("Could not release PostgreSQL scale advisory lock")
        connection.close()


def _redis_release_owned_capture(lock_key: str, owner: str) -> bool | None:
    backend = caches["default"]
    adapter = getattr(backend, "_cache", None)
    get_client = getattr(adapter, "get_client", None)
    serializer = getattr(adapter, "_serializer", None)
    if not callable(get_client) or serializer is None:
        return None

    key = backend.make_and_validate_key(lock_key)
    client = get_client(key, write=True)
    encoded_owner = serializer.dumps(owner)
    return bool(client.eval(_COMPARE_AND_DELETE, 1, key, encoded_owner))


def _claim_capture_lock(lock_key: str, owner: str, timeout: int) -> bool:
    backend = caches["default"]
    adapter = getattr(backend, "_cache", None)
    if callable(getattr(adapter, "get_client", None)):
        return bool(cache.add(lock_key, owner, timeout=timeout))
    # LocMemCache is process-local and its add is atomic. Guard both add and
    # compare-delete so local/test threads cannot delete a reacquired owner.
    with _LOCAL_CAPTURE_LOCK_GUARD:
        return bool(cache.add(lock_key, owner, timeout=timeout))


def _release_capture_lock(lock_key: str, owner: str) -> None:
    try:
        released = _redis_release_owned_capture(lock_key, owner)
        if released is not None:
            return
        with _LOCAL_CAPTURE_LOCK_GUARD:
            if cache.get(lock_key) == owner:
                cache.delete(lock_key)
    except Exception:  # pragma: no cover - cache outage during best-effort release
        log.exception("Could not release authoritative scale capture lock")


@contextmanager
def authoritative_capture(scale_key: str = DEFAULT_SCALE_KEY):
    """Serialize one authoritative physical read through its atomic apply.

    Production Redis provides a cross-worker lease and owner-safe Lua release.
    Local/test LocMemCache uses an equivalent process-local guarded fallback.
    """

    lock_key = authoritative_capture_lock_key(scale_key)
    owner = uuid4().hex
    lock_seconds = _capture_lock_seconds()
    lease_deadline = monotonic() + lock_seconds
    try:
        acquired = _claim_capture_lock(lock_key, owner, lock_seconds)
    except Exception as exc:
        raise TruckScaleUnavailable("Не удалось заблокировать весы.") from exc
    if not acquired:
        raise TruckScaleCaptureBusy()
    deadline_token = _CAPTURE_LEASE_DEADLINE.set(lease_deadline)
    database_locked = False
    try:
        database_locked = _claim_database_capture(scale_key)
        yield
    finally:
        try:
            if database_locked:
                _release_database_capture(scale_key)
        finally:
            try:
                _release_capture_lock(lock_key, owner)
            finally:
                _CAPTURE_LEASE_DEADLINE.reset(deadline_token)


def _api_url(scale_key: str = DEFAULT_SCALE_KEY) -> str:
    try:
        setting_name = _URL_SETTING_BY_SCALE[scale_key]
    except KeyError as exc:
        raise ValueError(f"Unknown truck scale: {scale_key}") from exc
    value = getattr(settings, setting_name, "")
    return value.strip() if isinstance(value, str) else ""


def enabled(scale_key: str = DEFAULT_SCALE_KEY) -> bool:
    """Whether production explicitly configured the scale integration."""
    return bool(_api_url(scale_key))


def _validated_api_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        # Accessing ``port`` also rejects malformed values before any I/O.
        _ = parsed.port
    except ValueError as exc:
        raise TruckScaleUnavailable(
            "Адрес весов настроен некорректно."
        ) from exc
    if (
        parsed.scheme not in ("http", "https")
        or not hostname
        or username is not None
        or password is not None
        or "#" in url
    ):
        raise TruckScaleUnavailable(
            "Адрес весов настроен некорректно."
        )
    return url


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never turn one scale read into a request to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_request(request: urllib.request.Request, timeout: float):
    # The scale address is infrastructure configuration, not a public URL.
    # Ignore HTTP(S)_PROXY from the host and never follow redirects from it.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)


def _configuration_decimal(name: str, *, positive: bool) -> Decimal:
    try:
        value = Decimal(str(getattr(settings, name)))
    except (AttributeError, InvalidOperation, TypeError, ValueError) as exc:
        raise TruckScaleUnavailable() from exc
    if not value.is_finite() or (value <= 0 if positive else value < 0):
        raise TruckScaleUnavailable()
    return value


def _configured_timeout(name: str) -> float:
    try:
        value = float(getattr(settings, name))
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise TruckScaleUnavailable() from exc
    if not math.isfinite(value) or value <= 0:
        raise TruckScaleUnavailable()
    return value


def _timeout() -> float:
    return _configured_timeout("TRUCK_SCALE_TIMEOUT_SECONDS")


def _preview_timeout() -> float:
    return _configured_timeout("TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS")


def _invalid_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _read_payload(url: str, *, timeout: float | None = None) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        with _open_request(
            request,
            timeout=_timeout() if timeout is None else timeout,
        ) as response:
            if getattr(response, "status", 200) != 200:
                raise TruckScaleUnavailable()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except TruckScaleUnavailable:
        raise
    except urllib.error.HTTPError as exc:
        exc.close()
        raise TruckScaleUnavailable() from exc
    except (
        http.client.HTTPException,
        TimeoutError,
        OSError,
        urllib.error.URLError,
        ValueError,
    ) as exc:
        raise TruckScaleUnavailable() from exc

    if len(raw) > MAX_RESPONSE_BYTES:
        raise TruckScaleMalformedResponse(
            "Ответ весов превышает допустимый размер."
        )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_invalid_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TruckScaleMalformedResponse() from exc
    if not isinstance(payload, dict):
        raise TruckScaleMalformedResponse()
    return payload


def _required_flag(payload: dict[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise TruckScaleMalformedResponse()
    return value


def _required_decimal(payload: dict[str, Any], name: str) -> Decimal:
    value = payload.get(name)
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TruckScaleMalformedResponse()
    return value


def _optional_decimal(payload: dict[str, Any], name: str) -> Decimal | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TruckScaleMalformedResponse()
    return value


def _normalized_preview_weight(weight: Decimal | None) -> Decimal | None:
    if weight is None:
        return None
    max_weight = _configuration_decimal(
        "TRUCK_SCALE_MAX_WEIGHT_KG", positive=True
    )
    if weight < 0 or weight > max_weight:
        return None
    try:
        return weight.quantize(WEIGHT_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise TruckScaleMalformedResponse() from exc


def read_truck_scale_observation(
    scale_key: str = DEFAULT_SCALE_KEY,
) -> ScaleObservation:
    """Read display state without weakening the authoritative capture path."""
    url = _api_url(scale_key)
    if not url:
        raise TruckScaleDisabled()

    payload = _read_payload(
        _validated_api_url(url),
        timeout=_preview_timeout(),
    )
    connected = _required_flag(payload, "connected")
    stable = _required_flag(payload, "stable")
    stale = _required_flag(payload, "stale")

    if "error" not in payload:
        raise TruckScaleMalformedResponse()
    error = payload["error"]
    if error is not None and not isinstance(error, str):
        raise TruckScaleMalformedResponse()

    if "updated_at" not in payload:
        raise TruckScaleMalformedResponse()
    updated_at = payload["updated_at"]
    if updated_at is not None and not isinstance(updated_at, str):
        raise TruckScaleMalformedResponse()

    weight = _normalized_preview_weight(
        _optional_decimal(payload, "weight_kg")
    )
    age = _optional_decimal(payload, "age_seconds")
    if age is not None and age < 0:
        raise TruckScaleMalformedResponse()

    if not connected:
        state = "disconnected"
    elif error not in (None, ""):
        state = "unavailable"
    elif stale:
        state = "stale"
    elif age is not None and age > _configuration_decimal(
        "TRUCK_SCALE_MAX_AGE_SECONDS", positive=False
    ):
        # Trust the local freshness limit even if an upstream implementation
        # accidentally leaves its own ``stale`` flag false.
        state = "stale"
        stale = True
    elif not stable:
        state = "unstable"
    elif weight is None or age is None:
        state = "malformed"
    else:
        state = "ready"

    # Never keep a number on screen when its origin is disconnected or stale.
    if state in {"disconnected", "unavailable", "stale", "malformed"}:
        weight = None

    return ScaleObservation(
        state=state,
        weight_kg=weight,
        connected=connected,
        stable=stable,
        stale=stale,
        age_seconds=age,
        updated_at=updated_at,
    )


def read_truck_scale(scale_key: str = DEFAULT_SCALE_KEY) -> ScaleReading:
    """Fetch one fresh, stable scale reading; never retry or accept stale data."""
    url = _api_url(scale_key)
    if not url:
        raise TruckScaleDisabled()

    payload = _read_payload(_validated_api_url(url))
    connected = _required_flag(payload, "connected")
    stable = _required_flag(payload, "stable")
    stale = _required_flag(payload, "stale")

    if "error" not in payload:
        raise TruckScaleMalformedResponse()
    error = payload["error"]
    if error is not None and not isinstance(error, str):
        raise TruckScaleMalformedResponse()
    if not connected or not stable or stale or error not in (None, ""):
        raise TruckScaleNotReady()

    weight = _required_decimal(payload, "weight_kg")
    age = _required_decimal(payload, "age_seconds")
    max_weight = _configuration_decimal(
        "TRUCK_SCALE_MAX_WEIGHT_KG", positive=True
    )
    max_age = _configuration_decimal(
        "TRUCK_SCALE_MAX_AGE_SECONDS", positive=False
    )

    if weight <= 0 or weight > max_weight:
        raise TruckScaleNotReady("Вес на весах вне допустимого диапазона.")
    if age < 0:
        raise TruckScaleMalformedResponse()
    if age > max_age:
        raise TruckScaleNotReady("Показание весов устарело.")

    if "updated_at" not in payload:
        raise TruckScaleMalformedResponse()
    updated_at = payload["updated_at"]
    if updated_at is not None and not isinstance(updated_at, str):
        raise TruckScaleMalformedResponse()

    try:
        normalized_weight = weight.quantize(
            WEIGHT_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    except InvalidOperation as exc:
        raise TruckScaleMalformedResponse() from exc
    if normalized_weight <= 0 or normalized_weight > max_weight:
        raise TruckScaleNotReady("Вес на весах вне допустимого диапазона.")

    return ScaleReading(
        weight_kg=normalized_weight,
        age_seconds=age,
        updated_at=updated_at,
    )
