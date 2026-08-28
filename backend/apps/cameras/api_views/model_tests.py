"""Superuser-only browser adapter for offline CV model experiments."""

import math
from numbers import Real
from typing import ClassVar
from uuid import UUID

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser

from .. import ai
from ..serializers import ModelTestStartSerializer, ModelTestStatusQuerySerializer

MODEL_TEST_STATUSES = frozenset({"queued", "running", "completed", "failed"})


def _contract_error_response() -> Response:
    return _response(
        status.HTTP_502_BAD_GATEWAY,
        {
            "detail": "AI-сервис вернул несовместимый контракт тестирования",
            "code": "model_test_contract_error",
        },
    )


def _finite_number(value) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _safe_model_filename(value) -> str | None:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        return None
    filename = value.replace("\\", "/").rsplit("/", 1)[-1]
    return filename or None


def _sanitize_info(payload: dict) -> dict | None:
    defaults = payload.get("defaults")
    limits = payload.get("limits")
    bundles = payload.get("bundles")
    active_processors = payload.get("active_processors")
    if (
        type(payload.get("enabled")) is not bool
        or not isinstance(defaults, dict)
        or not isinstance(defaults.get("line"), str)
        or defaults.get("direction")
        not in {"any", "up", "down", "positive", "negative"}
        or not _finite_number(defaults.get("inference_fps"))
        or defaults["inference_fps"] <= 0
        or not isinstance(limits, dict)
        or type(limits.get("max_upload_bytes")) is not int
        or limits["max_upload_bytes"] <= 0
        or not isinstance(bundles, list)
        or not isinstance(payload.get("device"), str)
        or type(payload.get("reject_while_processors_active")) is not bool
        or type(active_processors) is not int
        or active_processors < 0
        or payload.get("writes_production_analytics") is not False
    ):
        return None

    safe_bundles = []
    for bundle in bundles:
        if (
            not isinstance(bundle, dict)
            or not isinstance(bundle.get("id"), str)
            or type(bundle.get("ready")) is not bool
        ):
            return None
        safe_files = {
            key: _safe_model_filename(bundle.get(key))
            for key in ("detector", "color_classifier", "brand_classifier")
        }
        if any(value is None for value in safe_files.values()):
            return None
        safe_bundles.append({**bundle, **safe_files})

    public = dict(payload)
    public["bundles"] = safe_bundles
    public_limits = dict(limits)
    public_limits["max_upload_bytes"] = min(
        limits["max_upload_bytes"],
        settings.AI_MODEL_TEST_MAX_UPLOAD_BYTES,
    )
    public["limits"] = public_limits
    return public


def _valid_job_snapshot(payload: dict, expected_job_id: str) -> bool:
    if (
        payload.get("job_id") != expected_job_id
        or payload.get("status") not in MODEL_TEST_STATUSES
        or not isinstance(payload.get("config"), dict)
        or not isinstance(payload.get("progress"), dict)
        or not isinstance(payload.get("events"), list)
        or not isinstance(payload.get("page"), dict)
    ):
        return False
    return payload["status"] != "completed" or all(
        isinstance(payload.get(key), dict) for key in ("bundle", "input", "summary")
    )


def _detail_from_errors(errors) -> str:
    for messages in errors.values():
        if isinstance(messages, (list, tuple)) and messages:
            return str(messages[0])
        if messages:
            return str(messages)
    return "Проверьте параметры теста"


def _validated_query(request, serializer_class, allowed: set[str]):
    received = set(request.query_params)
    unknown = sorted(received - allowed)
    duplicates = sorted(
        key for key in received & allowed if len(request.query_params.getlist(key)) != 1
    )
    if unknown:
        return None, _response(
            status.HTTP_400_BAD_REQUEST,
            {
                "detail": f"Неизвестные параметры: {', '.join(unknown)}",
                "code": "invalid_model_test_request",
            },
        )
    if duplicates:
        return None, _response(
            status.HTTP_400_BAD_REQUEST,
            {
                "detail": f"Параметры нельзя повторять: {', '.join(duplicates)}",
                "code": "invalid_model_test_request",
            },
        )
    serializer = serializer_class(data=request.query_params)
    if not serializer.is_valid():
        return None, _response(
            status.HTTP_400_BAD_REQUEST,
            {
                "detail": _detail_from_errors(serializer.errors),
                "code": "invalid_model_test_request",
                "fields": serializer.errors,
            },
        )
    return serializer.validated_data, None


def _normalize_upstream(status_code: int, payload: dict) -> tuple[int, dict]:
    public = dict(payload)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return status.HTTP_502_BAD_GATEWAY, {
            "detail": "Сервис тестирования моделей настроен неверно",
            "code": "model_test_upstream_auth",
        }
    if status_code >= 400:
        detail = public.get("detail") or public.get("error")
        if not isinstance(detail, str) or not detail.strip():
            detail = f"AI-сервис вернул ошибку {status_code}"
        public["detail"] = detail
        public.setdefault("code", "model_test_upstream_error")
    return status_code, public


def _response(status_code: int, payload: dict) -> Response:
    status_code, payload = _normalize_upstream(status_code, payload)
    response = Response(payload, status=status_code)
    response["Cache-Control"] = "no-store"
    return response


def _unavailable_response() -> Response:
    return _response(
        status.HTTP_502_BAD_GATEWAY,
        {
            "detail": "AI-сервис камер недоступен",
            "code": "ai_unavailable",
        },
    )


class ModelTestCollectionView(APIView):
    """Advertise model bundles and stream one raw browser video upstream."""

    permission_classes: ClassVar[list[type]] = [IsSuperUser]
    parser_classes: ClassVar[list[type]] = []

    def get(self, request):
        if request.query_params:
            return _response(
                status.HTTP_400_BAD_REQUEST,
                {
                    "detail": "Этот запрос не принимает параметры",
                    "code": "invalid_model_test_request",
                },
            )
        if not ai.enabled():
            return _response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                {
                    "detail": "AI-сервис не настроен на сервере",
                    "code": "ai_disabled",
                },
            )
        try:
            status_code, payload = ai.model_test_info()
        except (ai.AiError, ai.AiUnavailable):
            return _unavailable_response()

        if status_code < 400:
            payload = _sanitize_info(payload)
            if payload is None:
                return _contract_error_response()
        return _response(status_code, payload)

    def post(self, request):
        if not ai.enabled():
            return _response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                {
                    "detail": "AI-сервис не настроен на сервере",
                    "code": "ai_disabled",
                },
            )
        query, error = _validated_query(
            request,
            ModelTestStartSerializer,
            {"bundle", "line", "direction", "inference_fps"},
        )
        if error is not None:
            return error

        if request.headers.get("Transfer-Encoding"):
            return _response(
                status.HTTP_400_BAD_REQUEST,
                {
                    "detail": "Передавайте видео с точным Content-Length",
                    "code": "invalid_model_test_upload",
                },
            )
        raw_length = request.headers.get("Content-Length", "").strip()
        if not raw_length:
            return _response(
                status.HTTP_411_LENGTH_REQUIRED,
                {
                    "detail": "Для видео обязателен Content-Length",
                    "code": "content_length_required",
                },
            )
        if (
            len(raw_length) > 20
            or not raw_length.isascii()
            or not raw_length.isdecimal()
        ):
            return _response(
                status.HTTP_400_BAD_REQUEST,
                {
                    "detail": "Content-Length должен быть одним целым числом",
                    "code": "invalid_content_length",
                },
            )
        content_length = int(raw_length)
        if content_length <= 0:
            return _response(
                status.HTTP_400_BAD_REQUEST,
                {"detail": "Видео не должно быть пустым", "code": "empty_video"},
            )
        if content_length > settings.AI_MODEL_TEST_MAX_UPLOAD_BYTES:
            return _response(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                {
                    "detail": "Видео превышает допустимый размер",
                    "code": "model_test_video_too_large",
                    "max_upload_bytes": settings.AI_MODEL_TEST_MAX_UPLOAD_BYTES,
                },
            )

        content_type = (
            request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if content_type not in ai.MODEL_TEST_CONTENT_TYPES:
            return _response(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                {
                    "detail": "Поддерживаются MP4, MOV, AVI и MKV",
                    "code": "unsupported_model_test_video",
                },
            )
        try:
            status_code, payload = ai.upload_model_test(
                request.stream,
                content_length=content_length,
                content_type=content_type,
                query=query,
            )
        except ai.ModelTestUploadInvalid as exc:
            return _response(
                exc.status,
                {"detail": exc.detail, "code": "invalid_model_test_upload"},
            )
        except ai.AiError:
            return _unavailable_response()
        except ai.AiUnavailable:
            return _unavailable_response()

        if status_code == status.HTTP_202_ACCEPTED:
            payload = dict(payload)
            job_id = payload.get("job_id")
            if (
                not isinstance(job_id, str)
                or payload.get("status") != "queued"
                or not isinstance(payload.get("bundle"), str)
                or payload.get("bundle") != query["bundle"]
            ):
                return _contract_error_response()
            try:
                canonical_job_id = str(UUID(job_id))
            except (AttributeError, TypeError, ValueError):
                return _contract_error_response()
            if canonical_job_id != job_id:
                return _contract_error_response()
            payload["status_url"] = f"/api/cameras/model-tests/{job_id}/"
        return _response(status_code, payload)


class ModelTestDetailView(APIView):
    """Poll one in-memory camera-PC test job and its paged final events."""

    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def get(self, request, job_id):
        query, error = _validated_query(
            request,
            ModelTestStatusQuerySerializer,
            {"after_event", "limit"},
        )
        if error is not None:
            return error
        if not ai.enabled():
            return _response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                {
                    "detail": "AI-сервис не настроен на сервере",
                    "code": "ai_disabled",
                },
            )
        try:
            status_code, payload = ai.model_test_status(str(job_id), **query)
        except (ai.AiError, ai.AiUnavailable):
            return _unavailable_response()
        if status_code < 400 and not _valid_job_snapshot(payload, str(job_id)):
            return _contract_error_response()
        return _response(status_code, payload)
