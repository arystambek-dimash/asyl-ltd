"""Strict HTTP client for the Grain site's physical truck scale."""

from __future__ import annotations

import http.client
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.conf import settings
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
