"""AI counting history and signed recording playback endpoints."""

from datetime import timedelta
from typing import ClassVar

from django.core import signing
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm
from apps.orders.models import Order
from apps.orders.querysets import for_post_board
from apps.sales.access import scope_by_client_department

from .. import recordings
from ..models import AiCountingSession, MonoblockCameraSettings
from ..policies import (
    active_device_for,
    can_control_session,
    session_started_by_name,
)

RECORDING_TOKEN_MAX_AGE = 10 * 60
RECORDING_TOKEN_SALT = "camera-recording"


class CameraAiSessionListView(APIView):
    """Открытые отгрузки для моноблока — по одной на каждую камеру."""

    def get_permissions(self):
        return [HasPerm("shipping.load", "shipping.view")]

    def get(self, request):
        open_sessions = (
            AiCountingSession.objects.filter(
                status__in=AiCountingSession.OPEN_STATUSES,
                order__in=Order.objects.all(),
            )
            .select_related("order__client__user", "started_by")
            .order_by("started_at")
        )
        open_sessions = scope_by_client_department(
            open_sessions,
            request.user,
            client_path="order__client",
        )
        device = active_device_for(request.user)
        if device is not None:
            open_sessions = open_sessions.filter(camera=device.camera_source)
        return Response(
            [
                {
                    "id": session.pk,
                    "order_id": session.order_id,
                    "order_client_name": session.order.client.name,
                    "order_truck_number": session.order.truck_number,
                    "camera": session.camera,
                    "status": session.status,
                    "started_at": session.started_at,
                    "started_by_id": session.started_by_id,
                    "started_by_name": session_started_by_name(session),
                    "can_stop": can_control_session(session, request.user),
                    "target_total": session.target_total,
                    "conveyor_enabled": session.conveyor_enabled,
                    "last_status": session.last_status,
                }
                for session in open_sessions
            ]
        )


def _recording_stream(session: AiCountingSession) -> str:
    if session.recording_stream:
        return session.recording_stream
    stream = (
        session.last_status.get("stream")
        if isinstance(session.last_status, dict)
        else ""
    )
    return stream if isinstance(stream, str) else ""


def _history_payload(session: AiCountingSession, names=None) -> dict:
    # names передаётся готовым словарём: иначе справочник подписей читается
    # заново на каждую из 500 строк истории.
    if names is None:
        names = MonoblockCameraSettings.display_names()
    last = session.last_status if isinstance(session.last_status, dict) else {}
    total = session.final_total
    if total is None:
        raw_total = last.get("total")
        total = raw_total if isinstance(raw_total, int) and raw_total >= 0 else None
    stream = _recording_stream(session)
    return {
        "id": session.pk,
        "order_id": session.order_id,
        "order_client_name": session.order.client.name,
        "order_truck_number": session.order.truck_number,
        "camera": session.camera,
        "camera_name": names.get(session.camera, session.camera),
        "status": session.status,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "started_by_id": session.started_by_id,
        "started_by_name": session_started_by_name(session),
        "final_total": total,
        "target_total": session.target_total,
        "conveyor_enabled": session.conveyor_enabled,
        "last_status": last,
        "has_recording": bool(stream),
        "recording_available_until": (
            (session.ended_at or timezone.now())
            + timedelta(days=recordings.VIDEO_RETENTION_DAYS)
            if stream
            else None
        ),
    }


def _history_queryset(user=None):
    queryset = (
        AiCountingSession.objects
        # Метаданные и финальный счёт остаются в БД вместе с заказом. Только
        # тяжёлые видеофайлы удаляются на ПК камер через 14 дней.
        .filter(order__in=Order.objects.all()).select_related(
            "order__client__user", "started_by"
        )
    )
    if user is None:
        return queryset
    return scope_by_client_department(
        queryset,
        user,
        client_path="order__client",
    )


class CameraAiSessionHistoryView(APIView):
    """Order-bound AI metadata; video itself follows the two-week retention."""

    def get_permissions(self):
        return [HasPerm("shipping.view")]

    def get(self, request):
        queryset = _history_queryset(request.user).order_by("-started_at")
        raw_order_ids = request.query_params.get("order_ids", "").strip()
        raw_order_id = request.query_params.get("order_id", "").strip()
        if raw_order_id:
            raw_order_ids = raw_order_id
        if request.query_params.get("post_board") == "1":
            settings = (
                MonoblockCameraSettings.objects.filter(singleton=True)
                .only("completed_orders_days")
                .first()
            )
            completed_days = settings.completed_orders_days if settings else 1
            queryset = queryset.filter(
                order__in=for_post_board(
                    scope_by_client_department(
                        Order.objects.all(),
                        request.user,
                        client_path="client",
                    ),
                    completed_days,
                )
            )
        elif raw_order_ids:
            parts = [part.strip() for part in raw_order_ids.split(",") if part.strip()]
            if len(parts) > 100:
                raise ValidationError(
                    {"detail": "Слишком много заказов", "code": "too_many_orders"}
                )
            try:
                order_ids = [int(part) for part in parts]
            except ValueError as exc:
                raise ValidationError(
                    {"detail": "Некорректный номер заказа", "code": "bad_order_id"}
                ) from exc
            queryset = queryset.filter(order_id__in=order_ids)
        names = MonoblockCameraSettings.display_names()
        return Response(
            [_history_payload(session, names) for session in queryset[:500]]
        )


def _history_session(pk: int, user=None) -> AiCountingSession:
    return get_object_or_404(_history_queryset(user), pk=pk)


def _session_segments(session: AiCountingSession) -> list[dict]:
    stream = _recording_stream(session)
    if not stream:
        return []
    if timezone.now() > (
        (session.ended_at or timezone.now())
        + timedelta(days=recordings.VIDEO_RETENTION_DAYS)
    ):
        return []
    start = session.activated_at or session.started_at
    end = (session.ended_at or timezone.now()) + timedelta(minutes=1)
    return recordings.list_segments(stream, start, end)


def _segment_video_url(session: AiCountingSession, segment: dict) -> str:
    token = signing.dumps(
        {
            "session": session.pk,
            "start": segment["start"],
            "duration": segment["duration"],
        },
        salt=RECORDING_TOKEN_SALT,
    )
    return f"/api/cameras/ai/history/{session.pk}/recording/video/?token={token}"


class CameraAiRecordingView(APIView):
    """List locally stored MediaMTX segments for one authorized session."""

    def get_permissions(self):
        return [HasPerm("shipping.view")]

    def get(self, request, pk: int):
        session = _history_session(pk, request.user)
        try:
            segments = _session_segments(session)
        except recordings.RecordingUnavailable:
            return Response(
                {
                    "available": False,
                    "detail": "Архив на компьютере камер сейчас недоступен",
                    "segments": [],
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "available": bool(segments),
                "retention_days": recordings.VIDEO_RETENTION_DAYS,
                "segments": [
                    {
                        **segment,
                        "video_url": _segment_video_url(session, segment),
                    }
                    for segment in segments
                ],
            }
        )


class CameraAiRecordingVideoView(APIView):
    """Stream a verified local segment without storing it on the web server."""

    # Native <video> cannot attach the JWT stored by the SPA. Access is
    # granted by a short-lived token issued only by the protected list API.
    permission_classes: ClassVar[list[type]] = [AllowAny]

    def get(self, request, pk: int):
        try:
            token_data = signing.loads(
                request.query_params.get("token", ""),
                salt=RECORDING_TOKEN_SALT,
                max_age=RECORDING_TOKEN_MAX_AGE,
            )
            if not isinstance(token_data, dict) or token_data.get("session") != pk:
                raise signing.BadSignature("wrong session")
            requested_start = str(token_data["start"])
            requested_duration = float(token_data["duration"])
        except (
            signing.BadSignature,
            signing.SignatureExpired,
            KeyError,
            TypeError,
            ValueError,
        ):
            return Response(
                {
                    "detail": "Ссылка на видео недействительна или устарела",
                    "code": "bad_recording_token",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Validate the bearer token before looking up the row. An invalid
        # public link must not reveal whether a counting session exists.
        session = _history_session(pk)
        try:
            segments = _session_segments(session)
        except recordings.RecordingUnavailable:
            return Response(
                {
                    "detail": "Архив на компьютере камер сейчас недоступен",
                    "code": "recording_unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        segment = next(
            (
                item
                for item in segments
                if item["start"] == requested_start
                and abs(item["duration"] - requested_duration) < 0.01
            ),
            None,
        )
        if segment is None:
            raise ValidationError(
                {"detail": "Фрагмент не найден", "code": "segment_not_found"}
            )
        try:
            upstream = recordings.open_segment(
                _recording_stream(session),
                segment["start"],
                segment["duration"],
            )
        except recordings.RecordingUnavailable:
            return Response(
                {
                    "detail": "Не удалось открыть видео на компьютере камер",
                    "code": "recording_unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        def chunks():
            try:
                while True:
                    chunk = upstream.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()

        response = StreamingHttpResponse(chunks(), content_type="video/mp4")
        length = upstream.headers.get("Content-Length")
        if length:
            response["Content-Length"] = length
        response["Content-Disposition"] = (
            f'inline; filename="loading-{session.order_id}.mp4"'
        )
        response["Cache-Control"] = "private, no-store"
        return response
