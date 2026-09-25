"""HTTPS ingestion of vehicle plate events from the camera PC."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import uuid
from datetime import UTC
from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, ParseError
from rest_framework.parsers import BaseParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.viewsets import NoStoreMixin
from config.throttles import VehiclePlateWebhookRateThrottle

from . import ai
from .api_views.responses import error_response
from .models import VehiclePlateEvent

log = logging.getLogger(__name__)

CAMERA_RE = re.compile(r"^cam[1-9][0-9]{0,28}$")
ISO8601_DATETIME_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))$"
)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

class PayloadTooLarge(APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "JSON body is too large"
    default_code = "payload_too_large"


def _reject_nonfinite_json(value: str):
    raise ValueError(f"non-finite JSON number: {value}")


class BoundedJSONParser(BaseParser):
    """Parse one JSON value without ever reading past the endpoint limit."""

    media_type = "application/json"

    def parse(self, stream, media_type=None, parser_context=None):
        del media_type
        limit = settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise PayloadTooLarge()
        encoding = (parser_context or {}).get("encoding", settings.DEFAULT_CHARSET)
        try:
            if isinstance(raw, bytes):
                raw = raw.decode(encoding)
            return json.loads(raw, parse_constant=_reject_nonfinite_json)
        except (LookupError, RecursionError, UnicodeDecodeError, ValueError) as exc:
            raise ParseError("Malformed JSON") from exc


def _parse_uuid(value, *, field: str) -> uuid.UUID:
    if not isinstance(value, str) or UUID_RE.fullmatch(value) is None:
        raise serializers.ValidationError({field: "Передайте корректный UUID"})
    return uuid.UUID(value)


def _decimal_number(
    value,
    *,
    field: str,
    minimum: Decimal,
    maximum: Decimal,
    quantum: Decimal,
) -> Decimal:
    if type(value) not in (int, float):
        raise serializers.ValidationError({field: "Передайте число"})
    if isinstance(value, float) and not math.isfinite(value):
        raise serializers.ValidationError({field: "Передайте конечное число"})
    parsed = Decimal(str(value))
    if parsed < minimum or parsed > maximum:
        raise serializers.ValidationError(
            {field: f"Допустимо значение от {minimum} до {maximum}"}
        )
    return parsed.quantize(quantum, rounding=ROUND_HALF_UP)


class VehiclePlateWebhookSerializer(serializers.Serializer):
    """Strictly validate the stable fields while accepting future additions."""

    schema_version = serializers.JSONField()
    event_id = serializers.JSONField()
    event_type = serializers.JSONField()
    detected_at = serializers.JSONField()
    vehicle_number = serializers.JSONField()
    camera = serializers.JSONField()
    source = serializers.JSONField()
    stationary_seconds = serializers.JSONField()
    confirmation = serializers.JSONField()

    def validate(self, attrs):
        if type(attrs["schema_version"]) is not int or attrs["schema_version"] != 1:
            raise serializers.ValidationError(
                {"schema_version": "Поддерживается только schema_version=1"}
            )
        if attrs["event_type"] != "vehicle_plate_detected":
            raise serializers.ValidationError(
                {"event_type": "Ожидается vehicle_plate_detected"}
            )

        event_id = _parse_uuid(attrs["event_id"], field="event_id")

        vehicle_number = attrs["vehicle_number"]
        if (
            not isinstance(vehicle_number, str)
            or ai.VEHICLE_PLATE_RE.fullmatch(vehicle_number) is None
        ):
            raise serializers.ValidationError(
                {"vehicle_number": "Ожидается формат 123ABC02, 160AL17 или X209LAN"}
            )

        camera = attrs["camera"]
        if not isinstance(camera, str) or CAMERA_RE.fullmatch(camera) is None:
            raise serializers.ValidationError({"camera": "Ожидается камера cam<N>"})

        source = attrs["source"]
        if source not in ("main", "sub"):
            raise serializers.ValidationError(
                {"source": "Допустимы только main или sub"}
            )

        raw_detected_at = attrs["detected_at"]
        if (
            not isinstance(raw_detected_at, str)
            or len(raw_detected_at) > 40
            or ISO8601_DATETIME_RE.fullmatch(raw_detected_at) is None
        ):
            raise serializers.ValidationError(
                {"detected_at": "Передайте дату ISO 8601 с часовым поясом"}
            )
        try:
            detected_at = parse_datetime(raw_detected_at)
            if detected_at is not None and timezone.is_aware(detected_at):
                detected_at = detected_at.astimezone(UTC)
        except (OverflowError, TypeError, ValueError):
            detected_at = None
        if detected_at is None or not timezone.is_aware(detected_at):
            raise serializers.ValidationError(
                {"detected_at": "Передайте дату ISO 8601 с часовым поясом"}
            )

        stationary_seconds = _decimal_number(
            attrs["stationary_seconds"],
            field="stationary_seconds",
            minimum=Decimal(3),
            maximum=Decimal(86400),
            quantum=Decimal("0.001"),
        )

        confirmation = attrs["confirmation"]
        if not isinstance(confirmation, dict):
            raise serializers.ValidationError(
                {"confirmation": "Передайте объект подтверждения"}
            )
        votes = confirmation.get("votes")
        if type(votes) is not int or not 3 <= votes <= 32767:
            raise serializers.ValidationError(
                {"confirmation": {"votes": "Допустимо целое число от 3 до 32767"}}
            )
        detector_confidence = _decimal_number(
            confirmation.get("detector_confidence"),
            field="detector_confidence",
            minimum=Decimal(0),
            maximum=Decimal(1),
            quantum=Decimal("0.0001"),
        )
        ocr_confidence = _decimal_number(
            confirmation.get("ocr_confidence"),
            field="ocr_confidence",
            minimum=Decimal(0),
            maximum=Decimal(1),
            quantum=Decimal("0.0001"),
        )

        return {
            "event_id": event_id,
            "vehicle_number": vehicle_number,
            "camera": camera,
            "source": source,
            "detected_at": detected_at,
            "stationary_seconds": stationary_seconds,
            "confirmation_votes": votes,
            "detector_confidence": detector_confidence,
            "ocr_confidence": ocr_confidence,
        }


def _log_value(raw, pattern: re.Pattern) -> str:
    """В лог попадает только значение строгого формата (ASCII, без мусора)."""
    return raw if isinstance(raw, str) and pattern.fullmatch(raw) else "-"


def _log_result(*, payload, http_status: int, result: str) -> None:
    payload = payload if isinstance(payload, dict) else {}
    log.info(
        "vehicle_plate_webhook event_id=%s vehicle_number=%s camera=%s "
        "http_status=%s result=%s",
        _log_value(payload.get("event_id"), UUID_RE),
        _log_value(payload.get("vehicle_number"), ai.VEHICLE_PLATE_RE),
        _log_value(payload.get("camera"), CAMERA_RE),
        http_status,
        result,
    )


def _authorized(request) -> bool:
    raw = request.META.get("HTTP_AUTHORIZATION", "")
    supplied = ""
    if isinstance(raw, str) and len(raw) <= 1024:
        parts = raw.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            supplied = parts[1]
    expected = settings.VEHICLE_PLATE_WEBHOOK_TOKEN
    supplied_digest = hashlib.sha256(supplied.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    matches = hmac.compare_digest(
        supplied_digest,
        expected_digest,
    )
    return bool(supplied and expected and matches)


class VehiclePlateWebhookView(NoStoreMixin, APIView):
    authentication_classes: ClassVar[list[type]] = []
    permission_classes: ClassVar[list[type]] = [AllowAny]
    throttle_classes: ClassVar[list[type]] = [VehiclePlateWebhookRateThrottle]
    parser_classes: ClassVar[list[type]] = [BoundedJSONParser]

    def handle_exception(self, exc):
        if isinstance(exc, PayloadTooLarge):
            _log_result(payload=None, http_status=413, result="payload_too_large")
        elif isinstance(exc, ParseError):
            _log_result(payload=None, http_status=400, result="malformed_json")
        return super().handle_exception(exc)

    def post(self, request):
        if not request.is_secure():
            _log_result(payload=None, http_status=400, result="https_required")
            return error_response(
                "HTTPS is required",
                "https_required",
                status.HTTP_400_BAD_REQUEST,
            )

        if not _authorized(request):
            _log_result(payload=None, http_status=401, result="unauthorized")
            response = error_response(
                "Invalid webhook credential",
                "authentication_failed",
                status.HTTP_401_UNAUTHORIZED,
            )
            response["WWW-Authenticate"] = "Bearer"
            return response

        # Размер тела ограничивает BoundedJSONParser: читает не больше лимита + 1.
        payload = request.data
        if not isinstance(payload, dict):
            _log_result(payload=None, http_status=400, result="invalid_payload")
            return error_response(
                "JSON body must be an object",
                "invalid_payload",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = VehiclePlateWebhookSerializer(data=payload)
        if not serializer.is_valid():
            _log_result(payload=payload, http_status=400, result="invalid_payload")
            return error_response(
                serializer.errors,
                "invalid_payload",
                status.HTTP_400_BAD_REQUEST,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        try:
            header_event_id = _parse_uuid(
                idempotency_key,
                field="Idempotency-Key",
            )
        except serializers.ValidationError:
            _log_result(
                payload=payload,
                http_status=400,
                result="invalid_idempotency_key",
            )
            return error_response(
                "Idempotency-Key must be a valid UUID",
                "invalid_idempotency_key",
                status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        if header_event_id != data["event_id"]:
            _log_result(
                payload=payload,
                http_status=400,
                result="idempotency_key_mismatch",
            )
            return error_response(
                "Idempotency-Key must match event_id",
                "idempotency_key_mismatch",
                status.HTTP_400_BAD_REQUEST,
            )

        defaults = {
            **data,
            "processing_status": VehiclePlateEvent.RECEIVED,
        }
        event_id = defaults.pop("event_id")
        try:
            with transaction.atomic():
                event, created = VehiclePlateEvent.objects.get_or_create(
                    event_id=event_id,
                    defaults=defaults,
                )
        except DatabaseError:
            _log_result(payload=payload, http_status=503, result="database_error")
            return error_response(
                "Temporary storage error",
                "temporary_storage_error",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        response_payload = {
            "ok": True,
            "duplicate": not created,
            "event_id": str(event_id),
        }
        if created:
            response_payload["vehicle_event_id"] = event.pk
        _log_result(
            payload=payload,
            http_status=http_status,
            result="created" if created else "duplicate",
        )
        return Response(response_payload, status=http_status)
