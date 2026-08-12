"""Strict HTTP client for the truck scale connected through Tailscale."""

from __future__ import annotations

import http.client
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from rest_framework.exceptions import APIException


MAX_RESPONSE_BYTES = 32 * 1024
WEIGHT_QUANTUM = Decimal("0.01")


class TruckScaleDisabled(APIException):
    status_code = 503
    default_detail = "Автомобильные весы не настроены."
    default_code = "truck_scale_disabled"


class TruckScaleUnavailable(APIException):
    status_code = 503
    default_detail = "Автомобильные весы сейчас недоступны."
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


def _api_url() -> str:
    value = getattr(settings, "TRUCK_SCALE_API_URL", "")
    return value.strip() if isinstance(value, str) else ""


def enabled() -> bool:
    """Whether production explicitly configured the scale integration."""
    return bool(_api_url())


def _validated_api_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        # Accessing ``port`` also rejects malformed values before any I/O.
        parsed.port
    except ValueError as exc:
        raise TruckScaleUnavailable(
            "Адрес автомобильных весов настроен некорректно."
        ) from exc
    if (
        parsed.scheme not in ("http", "https")
        or not hostname
        or username is not None
        or password is not None
        or "#" in url
    ):
        raise TruckScaleUnavailable(
            "Адрес автомобильных весов настроен некорректно."
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


def _timeout() -> float:
    try:
        value = float(getattr(settings, "TRUCK_SCALE_TIMEOUT_SECONDS"))
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise TruckScaleUnavailable() from exc
    if not math.isfinite(value) or value <= 0:
        raise TruckScaleUnavailable()
    return value


def _invalid_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _read_payload(url: str) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        with _open_request(request, timeout=_timeout()) as response:
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


def read_truck_scale() -> ScaleReading:
    """Fetch one fresh, stable scale reading; never retry or accept stale data."""
    url = _api_url()
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
