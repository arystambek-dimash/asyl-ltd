"""Strict HTTP client for the Grain site's physical truck scale."""

from __future__ import annotations

import http.client
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from time import monotonic
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, connection
from rest_framework.exceptions import APIException

from apps.common.locks import (
    acquire_advisory_lock,
    claim_owned_lease,
    release_advisory_lock,
    release_owned_lease,
)

MAX_RESPONSE_BYTES = 32 * 1024
WEIGHT_QUANTUM = Decimal("0.01")

# The Grain screen supports two independent hardware slots: railway wagons
# arrive with grain, while trucks collect outgoing cargo. A slot may be empty
# until its scale is installed; configuration never falls back across slots.
WAGON_SCALE_KEY = "wagon"
TRUCK_SCALE_KEY = "truck"
DEFAULT_SCALE_KEY = TRUCK_SCALE_KEY
SCALE_KEYS = frozenset({WAGON_SCALE_KEY, TRUCK_SCALE_KEY})
# Observation states whose weight is a live reading (settled or still moving);
# every other state carries no weight at all.
VALID_SCALE_STATES = frozenset({"ready", "unstable"})

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
    timeout = math.ceil(_timeout())
    return max(CAPTURE_LOCK_MIN_SECONDS, timeout + CAPTURE_LOCK_MARGIN_SECONDS)


def authoritative_db_timeout_ms() -> int:
    """Bound each PostgreSQL apply statement inside the remaining lease."""

    remaining_seconds = _capture_lock_seconds() - math.ceil(_timeout())
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


def _claim_database_capture(scale_key: str) -> None:
    """Try a PostgreSQL session lock that survives Redis lease expiry."""

    try:
        acquired = acquire_advisory_lock(
            CAPTURE_ADVISORY_NAMESPACE,
            _CAPTURE_ADVISORY_IDS[scale_key],
            blocking=False,
        )
    except DatabaseError as exc:
        connection.close()
        raise TruckScaleUnavailable(
            "Не удалось заблокировать весы в базе данных."
        ) from exc
    if not acquired:
        raise TruckScaleCaptureBusy()


@contextmanager
def authoritative_capture(scale_key: str = DEFAULT_SCALE_KEY):
    """Serialize one authoritative physical read through its atomic apply.

    The owned cache lease (``apps.common.locks``) keeps other workers out;
    the PostgreSQL advisory lock stays authoritative after the lease expires.
    """

    lock_key = authoritative_capture_lock_key(scale_key)
    owner = uuid4().hex
    lock_seconds = _capture_lock_seconds()
    lease_deadline = monotonic() + lock_seconds
    try:
        acquired = claim_owned_lease(lock_key, owner, lock_seconds)
    except Exception as exc:
        raise TruckScaleUnavailable("Не удалось заблокировать весы.") from exc
    if not acquired:
        raise TruckScaleCaptureBusy()
    deadline_token = _CAPTURE_LEASE_DEADLINE.set(lease_deadline)
    database_locked = False
    try:
        _claim_database_capture(scale_key)
        database_locked = True
        yield
    finally:
        try:
            if database_locked:
                # PostgreSQL also releases the session lock on worker death,
                # independently of the finite Redis lease.
                release_advisory_lock(
                    CAPTURE_ADVISORY_NAMESPACE, _CAPTURE_ADVISORY_IDS[scale_key]
                )
        finally:
            try:
                release_owned_lease(lock_key, owner)
            finally:
                _CAPTURE_LEASE_DEADLINE.reset(deadline_token)


def _api_url(scale_key: str) -> str:
    try:
        setting_name = _URL_SETTING_BY_SCALE[scale_key]
    except KeyError as exc:
        raise ValueError(f"Unknown truck scale: {scale_key}") from exc
    value = getattr(settings, setting_name, "")
    return value.strip() if isinstance(value, str) else ""


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
    """Never turn one local read into a request to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_local_request(request: urllib.request.Request, timeout: float):
    """Open a request to site infrastructure: the scale PC or the video relay.

    These addresses are infrastructure configuration, not public URLs:
    ignore HTTP(S)_PROXY from the host and never follow redirects from them.
    """
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


def _read_payload(scale_key: str, *, preview: bool = False) -> dict[str, Any]:
    """Fetch the scale PC's JSON object; ``preview`` uses the short timeout."""

    url = _api_url(scale_key)
    if not url:
        raise TruckScaleDisabled()
    url = _validated_api_url(url)
    timeout = _preview_timeout() if preview else _timeout()
    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        with open_local_request(request, timeout=timeout) as response:
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


def _nullable_string(payload: dict[str, Any], name: str) -> str | None:
    """A key the scale PC must always send, with a string or ``null`` value."""

    if name not in payload:
        raise TruckScaleMalformedResponse()
    value = payload[name]
    if value is not None and not isinstance(value, str):
        raise TruckScaleMalformedResponse()
    return value


def _status_flags(payload: dict[str, Any]) -> tuple[bool, bool, bool, str | None]:
    """Return ``(connected, stable, stale, error)`` of one scale response."""

    return (
        _required_flag(payload, "connected"),
        _required_flag(payload, "stable"),
        _required_flag(payload, "stale"),
        _nullable_string(payload, "error"),
    )


def _optional_decimal(payload: dict[str, Any], name: str) -> Decimal | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TruckScaleMalformedResponse()
    return value


def _required_decimal(payload: dict[str, Any], name: str) -> Decimal:
    value = _optional_decimal(payload, name)
    if value is None:
        raise TruckScaleMalformedResponse()
    return value


def _quantized_weight(weight: Decimal) -> Decimal:
    try:
        return weight.quantize(WEIGHT_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise TruckScaleMalformedResponse() from exc


def _normalized_preview_weight(weight: Decimal | None) -> Decimal | None:
    if weight is None:
        return None
    max_weight = _configuration_decimal(
        "TRUCK_SCALE_MAX_WEIGHT_KG", positive=True
    )
    if weight < 0 or weight > max_weight:
        return None
    return _quantized_weight(weight)


def read_truck_scale_observation(
    scale_key: str = DEFAULT_SCALE_KEY,
) -> ScaleObservation:
    """Read display state without weakening the authoritative capture path."""
    payload = _read_payload(scale_key, preview=True)
    connected, stable, stale, error = _status_flags(payload)
    updated_at = _nullable_string(payload, "updated_at")

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
    payload = _read_payload(scale_key)
    connected, stable, stale, error = _status_flags(payload)
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

    # Checked only after readiness: a not-ready scale reports "not ready",
    # not a malformed timestamp.
    updated_at = _nullable_string(payload, "updated_at")

    normalized_weight = _quantized_weight(weight)
    if normalized_weight <= 0 or normalized_weight > max_weight:
        raise TruckScaleNotReady("Вес на весах вне допустимого диапазона.")

    return ScaleReading(
        weight_kg=normalized_weight,
        age_seconds=age,
        updated_at=updated_at,
    )
