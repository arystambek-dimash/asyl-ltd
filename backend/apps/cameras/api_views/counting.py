"""HTTP adapters for the camera AI counting line."""

from typing import ClassVar

from django.http import HttpResponse
from django.http.response import HttpResponseBase
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser

from .. import ai, services
from .responses import error_response


def _ai_proxy_response(fn):
    """Return an AI response body/status intact without exposing credentials."""
    if not ai.enabled():
        return error_response(
            "AI-подсчёт не настроен на сервере", "ai_disabled", status.HTTP_503_SERVICE_UNAVAILABLE
        )
    try:
        result = fn()
    except ai.AiUnavailable:
        return error_response("AI-сервис камер недоступен", "ai_unavailable", status.HTTP_502_BAD_GATEWAY)
    except ai.AiError as exc:
        return error_response(
            exc.detail, "ai_error", exc.status if exc.status in (400, 401, 404, 503) else 502
        )
    if isinstance(result, HttpResponseBase):
        return result
    upstream_status, payload = result
    return Response(payload, status=upstream_status)


class CameraCountingLineView(APIView):
    """Superuser-only proxy for a camera's persisted counting line."""

    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def get(self, request, cam: str):
        # «Обновить статус» after a saved-but-not-applied PUT asks the running
        # processor too; the 3-second background poll does not.
        check_applied = request.query_params.get("applied") == "1"

        def read():
            upstream_status, payload = ai.counting_line(cam)
            result = ai.line_config_payload(upstream_status, payload)
            if check_applied and upstream_status < 400:
                result["line_applied"] = ai.counting_line_application(cam, payload)
            return upstream_status, result

        return _ai_proxy_response(read)

    def put(self, request, cam: str):
        # save_counting_line performs one PUT only. A 503 with saved=true is
        # deliberately passed to the browser without an automatic retry.
        def save():
            upstream_status, payload = ai.save_counting_line(cam, request.data)
            if payload.get("saved") is True:
                # A durable save is authoritative even when applying it to a
                # live processor returned 503. Inventory should show the saved
                # value, while fresh processor polls reveal what is applied.
                services.update_cached_counting_line(cam, payload)
                ai.invalidate_counting_line_caches()
            return upstream_status, ai.line_config_payload(upstream_status, payload)

        return _ai_proxy_response(save)


class CameraCountingLineFrameView(APIView):
    """Still frame for the line editor when live video does not connect."""

    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def get(self, request, cam: str):
        def frame():
            response = HttpResponse(
                ai.counting_line_frame(cam),
                content_type="image/jpeg",
            )
            response["Cache-Control"] = "no-store"
            return response

        return _ai_proxy_response(frame)
