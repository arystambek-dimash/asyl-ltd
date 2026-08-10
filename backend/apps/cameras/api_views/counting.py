"""HTTP adapters for order-bound camera AI counting."""

from typing import ClassVar

from apps.common.permissions import HasPerm, IsStaff, IsSuperUser
from apps.orders.models import Order
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import ai, counting, sessions
from ..serializers import CameraAiActionSerializer


def _busy_response(session, user=None) -> Response:
    return Response(
        {
            "detail": f"AI-подсчёт занят заказом #{session.order_id}",
            "code": "ai_busy",
            **counting.metadata(session, None, "", user),
            "running": False,
        },
        status=status.HTTP_409_CONFLICT,
    )


def _ai_response(fn, user=None):
    """Map failures from the camera AI client to the existing public API."""
    if not ai.enabled():
        return Response(
            {"detail": "AI-подсчёт не настроен на сервере", "code": "ai_disabled"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        return Response(fn())
    except ai.AiUnavailable:
        return Response(
            {"detail": "AI-сервис камер недоступен", "code": "ai_unavailable"},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    except ai.AiError as exc:
        http_status = (
            exc.status
            if exc.status in (400, 401, 404, 409, 503)
            else status.HTTP_502_BAD_GATEWAY
        )
        return Response(
            {"detail": exc.detail, "code": "ai_error"},
            status=http_status,
        )
    except sessions.AiSessionBusy as exc:
        return _busy_response(exc.session, user)


def _ai_proxy_response(fn):
    """Return an AI response body/status intact without exposing credentials."""
    if not ai.enabled():
        return Response(
            {"detail": "AI-подсчёт не настроен на сервере", "code": "ai_disabled"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        upstream_status, payload = fn()
        return Response(payload, status=upstream_status)
    except ai.AiUnavailable:
        return Response(
            {"detail": "AI-сервис камер недоступен", "code": "ai_unavailable"},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    except ai.AiError as exc:
        return Response(
            {"detail": exc.detail, "code": "ai_error"},
            status=exc.status if exc.status in (400, 401, 404, 503) else 502,
        )


def _order_id(request) -> int | None:
    """Read order_id with the legacy query, header, body precedence."""
    raw = (
        request.query_params.get("order_id")
        or request.headers.get("X-Order-Id")
        or request.data.get("order_id")
    )
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _loading_order(request) -> Order | None:
    order_id = _order_id(request)
    if order_id is None:
        return None
    return get_object_or_404(Order.objects.all(), pk=order_id)


def _complete_order(request) -> bool:
    raw = request.data.get(
        "complete_order",
        request.query_params.get("complete_order"),
    )
    return raw is True or str(raw).lower() in ("1", "true")


def _action_input(
    request,
    *,
    missing_order_detail: str,
    include_complete_order: bool = False,
) -> tuple[Order, dict]:
    """Validate canonical action values after preserving legacy input parsing."""
    order = _loading_order(request)
    if order is None:
        raise ai.AiError(400, missing_order_detail)

    data: dict[str, object] = {"order_id": order.pk}
    if include_complete_order:
        # Normalize only the two truthy spellings accepted by the old view;
        # DRF's BooleanField intentionally accepts a broader vocabulary.
        data["complete_order"] = _complete_order(request)
    serializer = CameraAiActionSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return order, serializer.validated_data


class CameraCountingLineView(APIView):
    """Superuser-only proxy for a camera's persisted counting line."""

    permission_classes: ClassVar[list[type]] = [IsSuperUser]

    def get(self, request, cam: str):
        return _ai_proxy_response(lambda: ai.counting_line(cam))

    def put(self, request, cam: str):
        # save_counting_line performs one PUT only. A 503 with saved=true is
        # deliberately passed to the browser without an automatic retry.
        return _ai_proxy_response(lambda: ai.save_counting_line(cam, request.data))


class CameraAiView(APIView):
    """Read, start, or stop one order-bound AI counter."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsStaff()]
        return [HasPerm("shipping.load")]

    def get(self, request, cam: str):
        return _ai_response(
            lambda: counting.get_status(cam, _order_id(request), request.user),
            request.user,
        )

    def post(self, request, cam: str):
        def start():
            order, _validated = _action_input(
                request,
                missing_order_detail="Укажите заказ для AI-подсчёта",
            )
            return counting.start(cam, order, request.user)

        return _ai_response(start, request.user)

    def delete(self, request, cam: str):
        def stop():
            order, validated = _action_input(
                request,
                missing_order_detail="Укажите заказ для завершения AI-сессии",
                include_complete_order=True,
            )
            return counting.stop(
                cam,
                order,
                request.user,
                complete_order=validated["complete_order"],
            )

        return _ai_response(stop, request.user)


class CameraAiResetView(APIView):
    """Reset the counter of one owned, running AI session."""

    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, cam: str):
        def reset():
            order, _validated = _action_input(
                request,
                missing_order_detail="Укажите заказ для сброса AI-счётчика",
            )
            return counting.reset(cam, order, request.user)

        return _ai_response(reset, request.user)
