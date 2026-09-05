"""HTTPS ingestion and authenticated CRM reads for vehicle plate events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import uuid
from datetime import UTC
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import ClassVar

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, ParseError, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import BaseParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.common.permissions import HasPerm
from apps.common.query_params import parse_iso_date, validate_date_range

from .models import VehiclePlateEvent

log = logging.getLogger(__name__)

VEHICLE_NUMBER_RE = re.compile(r"^(?:[0-9]{3}[A-Z]{2,3}[0-9]{2}|[A-Z][0-9]{3}[A-Z]{3})$")
VEHICLE_NUMBER_SEARCH_RE = re.compile(r"^[0-9A-Z]{1,8}$")
CAMERA_RE = re.compile(r"^cam[1-9][0-9]{0,28}$")
ISO8601_DATETIME_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))$"
)
MODEL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
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
    try:
        return uuid.UUID(value)
    except ValueError as exc:  # pragma: no cover - guarded by shape, retained for safety
        raise serializers.ValidationError(
            {field: "Передайте корректный UUID"}
        ) from exc


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
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise serializers.ValidationError({field: "Передайте число"}) from exc
    if not parsed.is_finite() or parsed < minimum or parsed > maximum:
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
            or VEHICLE_NUMBER_RE.fullmatch(vehicle_number) is None
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


class VehiclePlateEventSerializer(serializers.ModelSerializer):
    stationary_seconds = serializers.FloatField(read_only=True)
    detector_confidence = serializers.FloatField(read_only=True)
    ocr_confidence = serializers.FloatField(read_only=True)

    class Meta:
        model = VehiclePlateEvent
        fields = (
            "id",
            "event_id",
            "vehicle_number",
            "camera",
            "source",
            "detected_at",
            "stationary_seconds",
            "confirmation_votes",
            "detector_confidence",
            "ocr_confidence",
            "processing_status",
            "processing_attempts",
            "processing_action",
            "processing_error",
            "processing_started_at",
            "processed_at",
        )


class VehiclePlateWebhookRateThrottle(SimpleRateThrottle):
    scope = "vehicle_plate_webhook"

    def get_rate(self):
        return api_settings.DEFAULT_THROTTLE_RATES.get(self.scope)

    def get_cache_key(self, request, view):
        del view
        if self.rate is None:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class VehiclePlateEventPagination(PageNumberPagination):
    page_size = 50
    max_page_size = 200

    def get_page_size(self, request):
        raw = request.query_params.get("limit")
        if raw is None:
            raw = request.query_params.get("page_size")
        if raw is None:
            return self.page_size
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"detail": "limit должен быть целым числом", "code": "bad_limit"}
            ) from exc
        if not 1 <= value <= self.max_page_size:
            raise ValidationError(
                {
                    "detail": f"limit должен быть от 1 до {self.max_page_size}",
                    "code": "bad_limit",
                }
            )
        return value


def _bounded_json_number(value, *, minimum=0, maximum=100_000):
    if type(value) not in (int, float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not minimum <= value <= maximum:
        return None
    return value


def _project_bbox(raw):
    if not isinstance(raw, dict):
        return None
    projected = {}
    pixels = raw.get("pixels")
    if (
        isinstance(pixels, list)
        and len(pixels) == 4
        and all(
            type(value) is int and 0 <= value <= 100_000
            for value in pixels
        )
    ):
        projected["pixels"] = list(pixels)
    normalized = raw.get("normalized")
    if isinstance(normalized, dict):
        normalized_values = {
            key: _bounded_json_number(normalized.get(key), minimum=0, maximum=1)
            for key in ("x", "y", "w", "h")
        }
        if all(value is not None for value in normalized_values.values()):
            projected["normalized"] = normalized_values
    return projected or None


def _project_vehicle_roi(raw):
    if not isinstance(raw, dict) or raw.get("coordinate_space") != "normalized":
        return None
    raw_points = raw.get("points")
    if not isinstance(raw_points, list) or not 3 <= len(raw_points) <= 32:
        return None
    points = []
    for raw_point in raw_points:
        if not isinstance(raw_point, dict):
            return None
        x = _bounded_json_number(raw_point.get("x"), minimum=0, maximum=1)
        y = _bounded_json_number(raw_point.get("y"), minimum=0, maximum=1)
        if x is None or y is None:
            return None
        points.append({"x": x, "y": y})
    return {"coordinate_space": "normalized", "points": points}


def _project_image(raw):
    if not isinstance(raw, dict):
        return None
    width = raw.get("width")
    height = raw.get("height")
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= 100_000
        or not 1 <= height <= 100_000
    ):
        return None
    return {"width": width, "height": height}


def _project_models(raw):
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in ("detector", "ocr"):
        value = raw.get(key)
        if isinstance(value, str) and MODEL_IDENTIFIER_RE.fullmatch(value):
            result[key] = value
    return result or None


def _project_payload(payload: dict, validated: dict) -> dict:
    """Persist only the documented metadata schema, never arbitrary/media data."""

    projected = {
        "schema_version": 1,
        "event_id": str(validated["event_id"]),
        "event_type": "vehicle_plate_detected",
        "detected_at": validated["detected_at"].isoformat(),
        "vehicle_number": validated["vehicle_number"],
        "camera": validated["camera"],
        "source": validated["source"],
        "stationary_seconds": float(validated["stationary_seconds"]),
        "confirmation": {
            "votes": validated["confirmation_votes"],
            "detector_confidence": float(validated["detector_confidence"]),
            "ocr_confidence": float(validated["ocr_confidence"]),
        },
    }
    optional_metadata = (
        ("bbox", _project_bbox(payload.get("bbox"))),
        ("vehicle_roi", _project_vehicle_roi(payload.get("vehicle_roi"))),
        ("image", _project_image(payload.get("image"))),
        ("models", _project_models(payload.get("models"))),
    )
    for key, value in optional_metadata:
        if value is not None:
            projected[key] = value
    return projected


def _safe_log_value(value, *, maximum: int = 80) -> str:
    if not isinstance(value, str):
        return "-"
    cleaned = "".join(
        char if 32 <= ord(char) < 127 else "?" for char in str(value)
    )
    return cleaned[:maximum] or "-"


def _log_result(*, payload, http_status: int, result: str) -> None:
    payload = payload if isinstance(payload, dict) else {}
    raw_event_id = payload.get("event_id")
    raw_vehicle_number = payload.get("vehicle_number")
    raw_camera = payload.get("camera")
    log.info(
        "vehicle_plate_webhook event_id=%s vehicle_number=%s camera=%s "
        "http_status=%s result=%s",
        _safe_log_value(
            raw_event_id
            if isinstance(raw_event_id, str) and UUID_RE.fullmatch(raw_event_id)
            else None
        ),
        _safe_log_value(
            raw_vehicle_number
            if isinstance(raw_vehicle_number, str)
            and VEHICLE_NUMBER_RE.fullmatch(raw_vehicle_number)
            else None
        ),
        _safe_log_value(
            raw_camera
            if isinstance(raw_camera, str) and CAMERA_RE.fullmatch(raw_camera)
            else None
        ),
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


def _error_response(detail: str, code: str, http_status: int) -> Response:
    response = Response(
        {"detail": detail, "code": code},
        status=http_status,
    )
    response["Cache-Control"] = "no-store"
    return response


class VehiclePlateWebhookView(APIView):
    authentication_classes: ClassVar[list[type]] = []
    permission_classes: ClassVar[list[type]] = [AllowAny]
    throttle_classes: ClassVar[list[type]] = [VehiclePlateWebhookRateThrottle]
    parser_classes: ClassVar[list[type]] = [BoundedJSONParser]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def handle_exception(self, exc):
        if isinstance(exc, PayloadTooLarge):
            _log_result(payload=None, http_status=413, result="payload_too_large")
        elif isinstance(exc, ParseError):
            _log_result(payload=None, http_status=400, result="malformed_json")
        return super().handle_exception(exc)

    def post(self, request):
        if not request.is_secure():
            _log_result(payload=None, http_status=400, result="https_required")
            return _error_response(
                "HTTPS is required",
                "https_required",
                status.HTTP_400_BAD_REQUEST,
            )

        if not _authorized(request):
            _log_result(payload=None, http_status=401, result="unauthorized")
            response = _error_response(
                "Invalid webhook credential",
                "authentication_failed",
                status.HTTP_401_UNAUTHORIZED,
            )
            response["WWW-Authenticate"] = "Bearer"
            return response

        raw_length = request.META.get("CONTENT_LENGTH")
        if raw_length not in (None, ""):
            try:
                content_length = int(raw_length)
            except (TypeError, ValueError):
                _log_result(payload=None, http_status=400, result="bad_content_length")
                return _error_response(
                    "Invalid Content-Length",
                    "bad_content_length",
                    status.HTTP_400_BAD_REQUEST,
                )
            if content_length < 0:
                _log_result(payload=None, http_status=400, result="bad_content_length")
                return _error_response(
                    "Invalid Content-Length",
                    "bad_content_length",
                    status.HTTP_400_BAD_REQUEST,
                )
            if content_length > settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES:
                raise PayloadTooLarge()

        payload = request.data
        if not isinstance(payload, dict):
            _log_result(payload=None, http_status=400, result="invalid_payload")
            return _error_response(
                "JSON body must be an object",
                "invalid_payload",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = VehiclePlateWebhookSerializer(data=payload)
        if not serializer.is_valid():
            _log_result(payload=payload, http_status=400, result="invalid_payload")
            return _error_response(
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
            return _error_response(
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
            return _error_response(
                "Idempotency-Key must match event_id",
                "idempotency_key_mismatch",
                status.HTTP_400_BAD_REQUEST,
            )

        defaults = {
            **data,
            "payload_json": _project_payload(payload, data),
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
            return _error_response(
                "Temporary storage error",
                "temporary_storage_error",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        canonical_event_id = str(event_id)
        try:
            # Local import avoids making the camera ingestion model depend on
            # Grain at module import time. The event is already committed before
            # the one-shot physical capture; a failed capture remains available
            # for manual review but is never retried against a later vehicle.
            from apps.grain.services import process_vehicle_plate_event

            automation = process_vehicle_plate_event(
                event.pk,
                allow_capture=created,
            )
        except DatabaseError:
            log.exception(
                "vehicle plate automation database error event_id=%s",
                canonical_event_id,
            )
            _log_result(
                payload=payload,
                http_status=503,
                result="automation_database_error",
            )
            return _error_response(
                "Temporary automation storage error",
                "temporary_automation_error",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:  # pragma: no cover - final safety boundary
            log.exception(
                "vehicle plate automation failed event_id=%s",
                canonical_event_id,
            )
            _log_result(
                payload=payload,
                http_status=503,
                result="automation_error",
            )
            return _error_response(
                "Temporary automation error",
                "temporary_automation_error",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if automation.retryable:
            _log_result(
                payload=payload,
                http_status=503,
                result="automation_retry",
            )
            response = Response(
                {
                    "ok": False,
                    "duplicate": not created,
                    "event_id": canonical_event_id,
                    "detail": "Temporary vehicle automation error",
                    "code": "vehicle_plate_automation_retry",
                    "automation": automation.as_payload(),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
            response["Cache-Control"] = "no-store"
            return response

        http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        result = "created" if created else "duplicate"
        response_payload = {
            "ok": True,
            "duplicate": not created,
            "event_id": canonical_event_id,
        }
        if created:
            response_payload["vehicle_event_id"] = event.pk
        # Preserve the original v1 response byte-for-byte while automation is
        # disabled locally. Enabled production responses add only one optional
        # backwards-compatible object.
        if automation.status != "disabled":
            response_payload["automation"] = automation.as_payload()
            result = f"{result}_{automation.status}"
        _log_result(payload=payload, http_status=http_status, result=result)
        return Response(
            response_payload,
            status=http_status,
            headers={"Cache-Control": "no-store"},
        )


class VehiclePlateEventListView(APIView):
    def get_permissions(self):
        return [HasPerm("events.view")]

    def get(self, request):
        params = request.query_params
        date_from = parse_iso_date(params.get("date_from"))
        date_to = parse_iso_date(params.get("date_to"))
        validate_date_range(date_from, date_to)

        queryset = VehiclePlateEvent.objects.all()
        if date_from:
            queryset = queryset.filter(detected_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(detected_at__date__lte=date_to)

        raw_vehicle_number = params.get("vehicle_number")
        if raw_vehicle_number not in (None, ""):
            if not isinstance(raw_vehicle_number, str):  # pragma: no cover
                raise ValidationError(
                    {"detail": "Некорректный номер машины", "code": "bad_plate"}
                )
            vehicle_number = raw_vehicle_number.strip().upper()
            if VEHICLE_NUMBER_SEARCH_RE.fullmatch(vehicle_number) is None:
                raise ValidationError(
                    {"detail": "Некорректный номер машины", "code": "bad_plate"}
                )
            queryset = queryset.filter(vehicle_number__icontains=vehicle_number)

        raw_camera = params.get("camera")
        if raw_camera not in (None, ""):
            if not isinstance(raw_camera, str) or CAMERA_RE.fullmatch(raw_camera) is None:
                raise ValidationError(
                    {"detail": "Некорректная камера", "code": "bad_camera"}
                )
            queryset = queryset.filter(camera=raw_camera)

        paginator = VehiclePlateEventPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serialized = VehiclePlateEventSerializer(page, many=True)
        response = paginator.get_paginated_response(serialized.data)
        response["Cache-Control"] = "no-store"
        return response
