from datetime import timedelta

from django.core import signing
from django.core.signing import TimestampSigner
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff
from apps.orders.models import Order
from apps.shipments.services import begin_camera_loading

from . import ai, health, recordings, services, sessions
from .models import AiCountingSession, MonoblockCameraSettings

CAM_COOKIE = "cam_token"
CAM_TOKEN_MAX_AGE = 12 * 3600  # секунд
_signer = TimestampSigner(salt="cameras")
RECORDING_TOKEN_MAX_AGE = 10 * 60
RECORDING_TOKEN_SALT = "camera-recording"


class CameraListView(APIView):
    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsStaff()]
        return [HasPerm("rbac.manage")]

    def get(self, request):
        names = MonoblockCameraSettings.display_names()
        cameras = [
            {
                **camera,
                "zone": names.get(camera.get("src"), camera.get("zone")),
            }
            for camera in services.discover_cameras()
        ]
        return Response(cameras)

    def patch(self, request):
        raw_source = request.data.get("camera")
        raw_name = request.data.get("name")
        if not isinstance(raw_source, str) or not isinstance(raw_name, str):
            raise ValidationError({
                "detail": "Передайте камеру и новое имя",
                "code": "bad_camera_name",
            })
        try:
            source = services.normalize_camera_path(raw_source)
        except ValueError:
            raise ValidationError({
                "detail": "Неизвестная камера",
                "code": "bad_camera",
            })

        name = " ".join(raw_name.split())
        if not name:
            raise ValidationError({
                "detail": "Название камеры не может быть пустым",
                "code": "empty_camera_name",
            })
        if len(name) > 80:
            raise ValidationError({
                "detail": "Название камеры не должно превышать 80 символов",
                "code": "camera_name_too_long",
            })

        row, _ = MonoblockCameraSettings.objects.get_or_create(singleton=True)
        names = row.camera_names if isinstance(row.camera_names, dict) else {}
        row.camera_names = {**names, source: name}
        row.updated_by = request.user
        row.save(update_fields=["camera_names", "updated_by", "updated_at"])
        return Response({"camera": source, "name": name})


class CameraTokenView(APIView):
    permission_classes = [IsStaff]

    def post(self, request):
        resp = Response(status=status.HTTP_204_NO_CONTENT)
        resp.set_cookie(
            CAM_COOKIE,
            _signer.sign(str(request.user.pk)),
            max_age=CAM_TOKEN_MAX_AGE,
            httponly=True,
            secure=request.is_secure(),
            samesite="Lax",
            path="/go2rtc/",
        )
        return resp


class MonoblockCameraSettingsView(APIView):
    """Shared allowlist for the camera dropdown in the Monoblock screen."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [HasPerm("shipping.load", "rbac.manage")]
        return [HasPerm("rbac.manage")]

    @staticmethod
    def _payload(settings_row=None):
        row = settings_row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        return {
            "camera_sources": row.camera_sources if row else [],
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        return Response(self._payload())

    def put(self, request):
        raw_sources = request.data.get("camera_sources")
        if not isinstance(raw_sources, list):
            raise ValidationError({
                "camera_sources": "Передайте список камер",
                "code": "bad_camera_sources",
            })

        normalized = []
        for raw in raw_sources:
            if not isinstance(raw, str):
                raise ValidationError({
                    "camera_sources": "Каждая камера должна быть строкой",
                    "code": "bad_camera_source",
                })
            try:
                source = ai.normalize(raw)
            except ai.AiError:
                raise ValidationError({
                    "camera_sources": f"Неизвестная камера: {raw}",
                    "code": "bad_camera_source",
                })
            if source not in normalized:
                normalized.append(source)

        row, _ = MonoblockCameraSettings.objects.update_or_create(
            singleton=True,
            defaults={"camera_sources": normalized, "updated_by": request.user},
        )
        return Response(self._payload(row))


class ShippingBoardSettingsView(APIView):
    """Admin policy for the live shipping board."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [HasPerm("shipping.view", "rbac.manage")]
        return [HasPerm("rbac.manage")]

    @staticmethod
    def _payload(row=None):
        row = row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        return {
            "completed_orders_days": row.completed_orders_days if row else 1,
            "video_retention_days": recordings.VIDEO_RETENTION_DAYS,
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        return Response(self._payload())

    def patch(self, request):
        value = request.data.get("completed_orders_days")
        if isinstance(value, bool):
            value = None
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValidationError({
                "completed_orders_days": "Укажите количество дней от 1 до 90",
                "code": "bad_completed_orders_days",
            })
        if value < 1 or value > 90:
            raise ValidationError({
                "completed_orders_days": "Допустимо от 1 до 90 дней",
                "code": "bad_completed_orders_days",
            })
        row, _ = MonoblockCameraSettings.objects.update_or_create(
            singleton=True,
            defaults={
                "completed_orders_days": value,
                "updated_by": request.user,
            },
        )
        return Response(self._payload(row))

    put = patch


class CameraHealthView(APIView):
    """Staff-facing monitor state; full outage/stale heartbeat is HTTP 503."""

    permission_classes = [IsStaff]

    def get(self, request):
        payload = health.state_payload()
        http_status = (
            status.HTTP_200_OK
            if health.exit_code(payload) == 0
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(payload, status=http_status)


class CameraAuthView(APIView):
    """Internal-эндпоинт для nginx auth_request — только проверка cookie."""

    authentication_classes = []
    permission_classes = [AllowAny]
    # Subrequest'ы nginx приходят без X-Forwarded-For и делили бы один
    # anon-бакет на всех зрителей — 429 здесь гасил бы всю камерную стену.
    throttle_classes = []

    def get(self, request):
        token = request.COOKIES.get(CAM_COOKIE, "")
        try:
            _signer.unsign(token, max_age=CAM_TOKEN_MAX_AGE)
        except signing.BadSignature:
            return Response(status=status.HTTP_403_FORBIDDEN)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _ai_response(fn, user=None):
    """Вызов клиента ai_service с маппингом его ошибок в HTTP-ответы."""
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
    except ai.AiError as e:
        http = e.status if e.status in (400, 404, 409) else status.HTTP_502_BAD_GATEWAY
        return Response({"detail": e.detail, "code": "ai_error"}, status=http)
    except sessions.AiSessionBusy as e:
        return _busy_response(e.session, user)


def _order_id(request) -> int | None:
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


def _started_by_name(session) -> str:
    user = session.started_by
    if user is None:
        return "Система"
    employee = getattr(user, "employee", None)
    return employee.name if employee and employee.name else user.username


def _can_control(session, user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_superuser
            or user.has_perm_code("rbac.manage")
            or session.started_by_id == user.pk
        )
    )


def _meta(session, order_id: int | None, camera: str, user=None) -> dict:
    if session is None:
        return {
            "available": True,
            "busy": False,
            "owned_by_order": False,
        }
    owner = session.order_id == order_id and session.camera == camera
    return {
        "available": owner,
        "busy": not owner,
        "owned_by_order": owner,
        "session_id": session.pk,
        "session_order_id": session.order_id,
        "session_camera": session.camera,
        "session_started_at": session.started_at,
        "session_started_by_id": session.started_by_id,
        "session_started_by_name": _started_by_name(session),
        "can_stop": _can_control(session, user),
    }


def _busy_response(session, user=None) -> Response:
    return Response(
        {
            "detail": f"AI-подсчёт занят заказом #{session.order_id}",
            "code": "ai_busy",
            **_meta(session, None, "", user),
            "running": False,
        },
        status=status.HTTP_409_CONFLICT,
    )


def _release_camera_binding(order_id: int, camera: str) -> None:
    """Освободить только совпадающую активную привязку, не трогая историю."""
    Order.objects.filter(
        pk=order_id,
        loading_camera=camera,
        status__in=("confirmed", "arrived", "loading"),
    ).update(loading_camera="")


class CameraAiView(APIView):
    """AI-подсчёт мешков: статус, включение и выключение модели на камере.

    `cam` — NVR-путь камеры у ai_service/MediaMTX, строго cam<N>.
    """

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsStaff()]
        return [HasPerm("shipping.load")]

    def get(self, request, cam: str):
        def get_status():
            camera = ai.normalize(cam)
            order_id = _order_id(request)
            session = sessions.current_for_camera(camera)
            metadata = _meta(session, order_id, camera, request.user)

            # A different order never touches the GPU worker. Its polling is a
            # cheap DB lookup and only reports who owns this camera's slot.
            if session and not metadata["owned_by_order"]:
                return {"running": False, **metadata}
            if not session:
                return {"running": False, **metadata}

            live = ai.status(camera)
            if live is None or not live.get("running", False):
                # The worker disappeared/restarted: release the stale slot so
                # this or another order can start again immediately.
                sessions.fail(session, "AI processor stopped unexpectedly")
                _release_camera_binding(session.order_id, camera)
                return {
                    "running": False,
                    "available": True,
                    "busy": False,
                    "owned_by_order": False,
                    "code": "ai_processor_stopped",
                }
            sessions.update_status(session, live)
            return {**live, **metadata}

        return _ai_response(get_status, request.user)

    def post(self, request, cam: str):
        def start():
            camera = ai.normalize(cam)
            order = _loading_order(request)
            if order is None:
                raise ai.AiError(400, "Укажите заказ для AI-подсчёта")

            # Конфликт владения важнее проверки статуса: оператор должен
            # увидеть, какой именно заказ уже занял камеру (HTTP 409), а не
            # безликое сообщение о недопустимом переходе.
            camera_session = sessions.current_for_camera(camera)
            if camera_session and camera_session.order_id != order.pk:
                raise sessions.AiSessionBusy(camera_session)
            order_session = sessions.current_for_order(order.pk)
            if order_session and order_session.camera != camera:
                raise sessions.AiSessionBusy(order_session)

            restoring_same_binding = (
                order.status in ("arrived", "loading")
                and order.loading_camera == camera
            )
            if order.status != "confirmed" and not restoring_same_binding:
                raise ai.AiError(
                    400,
                    "Для новой отгрузки выберите заказ в статусе «Ожидание въезда»",
                )
            if camera not in MonoblockCameraSettings.allowed_sources():
                raise ai.AiError(
                    400,
                    "Эта камера не разрешена администратором для Моноблока",
                )

            session, created = sessions.reserve(order, camera, request.user)

            try:
                order = begin_camera_loading(order, camera, request.user)
            except ValidationError as exc:
                if created:
                    sessions.fail(session, str(exc.detail))
                raise

            try:
                # A new reservation calls POST directly: ai_service already
                # makes it idempotent, so the old preliminary GET only added a
                # network round-trip (and up to a 10 second delay).
                live = ai.start(camera) if created else ai.status(camera)
                if live is None or not live.get("running", False):
                    live = ai.start(camera)
                sessions.activate(session, live)
                return {**live, **_meta(session, order.pk, camera, request.user)}
            except ai.AiUnavailable:
                # A timeout is ambiguous: the Windows worker may have accepted
                # POST and still be warming the model. Keep ownership so a
                # second order cannot start on the same GPU; owner polling will
                # reconcile the processor as soon as the service answers.
                raise
            except ai.AiError as e:
                # Deterministic client/limit errors mean no usable processor
                # was started. 5xx responses are ambiguous like a timeout.
                if e.status < 500:
                    sessions.fail(session, str(e))
                    _release_camera_binding(order.pk, camera)
                raise

        return _ai_response(start, request.user)

    def delete(self, request, cam: str):
        def stop():
            camera = ai.normalize(cam)
            order = _loading_order(request)
            if order is None:
                raise ai.AiError(400, "Укажите заказ для завершения AI-сессии")
            session = sessions.current_for_camera(camera)
            if session is None:
                return {"running": False, **_meta(None, order.pk, camera, request.user)}
            if session.order_id != order.pk:
                raise sessions.AiSessionBusy(session)
            if not _can_control(session, request.user):
                raise PermissionDenied(
                    "Остановить отгрузку может только начавший её сотрудник или администратор"
                )
            # The worker owns the only live copy of the final count. Persist
            # the GET snapshot before DELETE switches it to IDLE.
            final = ai.status(camera)
            sessions.commit_final(session, final)
            if final is not None:
                ai.delete(camera)
            sessions.finish(session, request.user, final)
            _release_camera_binding(order.pk, camera)
            return {
                **(final or {}),
                "running": False,
                "available": True,
                "busy": False,
                "owned_by_order": False,
            }
        return _ai_response(stop, request.user)


class CameraAiResetView(APIView):
    """Обнулить счётчик работающей модели — новая погрузка на той же камере."""

    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, cam: str):
        def reset():
            camera = ai.normalize(cam)
            order = _loading_order(request)
            if order is None:
                raise ai.AiError(400, "Укажите заказ для сброса AI-счётчика")
            session = sessions.current_for_camera(camera)
            if session is None or session.order_id != order.pk:
                if session:
                    raise sessions.AiSessionBusy(session)
                raise ai.AiError(409, "Активная AI-сессия не найдена")
            if not _can_control(session, request.user):
                raise PermissionDenied(
                    "Сбросить счётчик может только начавший отгрузку сотрудник или администратор"
                )
            live = ai.reset(camera)
            sessions.update_status(session, live)
            return {**live, **_meta(session, order.pk, camera, request.user)}

        return _ai_response(reset, request.user)


class CameraAiSessionListView(APIView):
    """Открытые отгрузки для моноблока — по одной на каждую камеру."""

    def get_permissions(self):
        return [HasPerm("shipping.load", "shipping.view")]

    def get(self, request):
        open_sessions = (
            AiCountingSession.objects
            .filter(
                status__in=AiCountingSession.OPEN_STATUSES,
                order__in=Order.objects.all(),
            )
            .select_related("order__client", "started_by__employee")
            .order_by("started_at")
        )
        return Response([
            {
                "id": session.pk,
                "order_id": session.order_id,
                "order_client_name": session.order.client.name,
                "order_truck_number": session.order.truck_number,
                "camera": session.camera,
                "status": session.status,
                "started_at": session.started_at,
                "started_by_id": session.started_by_id,
                "started_by_name": _started_by_name(session),
                "can_stop": _can_control(session, request.user),
                "last_status": session.last_status,
            }
            for session in open_sessions
        ])


def _recording_stream(session: AiCountingSession) -> str:
    if session.recording_stream:
        return session.recording_stream
    stream = session.last_status.get("stream") if isinstance(session.last_status, dict) else ""
    return stream if isinstance(stream, str) else ""


def _history_payload(session: AiCountingSession) -> dict:
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
        "started_by_name": _started_by_name(session),
        "final_total": total,
        "last_status": last,
        "has_recording": bool(stream),
        "recording_available_until": (
            (session.ended_at or timezone.now())
            + timedelta(days=recordings.VIDEO_RETENTION_DAYS)
            if stream else None
        ),
    }


def _history_queryset():
    return (
        AiCountingSession.objects
        # Метаданные и финальный счёт остаются в БД вместе с заказом. Только
        # тяжёлые видеофайлы удаляются на ПК камер через 14 дней.
        .filter(order__in=Order.objects.all())
        .select_related("order__client", "started_by__employee")
    )


class CameraAiSessionHistoryView(APIView):
    """Order-bound AI metadata; video itself follows the two-week retention."""

    def get_permissions(self):
        return [HasPerm("shipping.view")]

    def get(self, request):
        queryset = _history_queryset().order_by("-started_at")
        raw_order_ids = request.query_params.get("order_ids", "").strip()
        raw_order_id = request.query_params.get("order_id", "").strip()
        if raw_order_id:
            raw_order_ids = raw_order_id
        if raw_order_ids:
            parts = [part.strip() for part in raw_order_ids.split(",") if part.strip()]
            if len(parts) > 100:
                raise ValidationError({"detail": "Слишком много заказов", "code": "too_many_orders"})
            try:
                order_ids = [int(part) for part in parts]
            except ValueError:
                raise ValidationError({"detail": "Некорректный номер заказа", "code": "bad_order_id"})
            queryset = queryset.filter(order_id__in=order_ids)
        return Response([_history_payload(session) for session in queryset[:500]])


def _history_session(pk: int) -> AiCountingSession:
    return get_object_or_404(_history_queryset(), pk=pk)


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
    token = signing.dumps({
        "session": session.pk,
        "start": segment["start"],
        "duration": segment["duration"],
    }, salt=RECORDING_TOKEN_SALT)
    return f"/api/cameras/ai/history/{session.pk}/recording/video/?token={token}"


class CameraAiRecordingView(APIView):
    """List locally stored MediaMTX segments for one authorized session."""

    def get_permissions(self):
        return [HasPerm("shipping.view")]

    def get(self, request, pk: int):
        session = _history_session(pk)
        try:
            segments = _session_segments(session)
        except recordings.RecordingUnavailable:
            return Response({
                "available": False,
                "detail": "Архив на компьютере камер сейчас недоступен",
                "segments": [],
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({
            "available": bool(segments),
            "retention_days": recordings.VIDEO_RETENTION_DAYS,
            "segments": [
                {
                    **segment,
                    "video_url": _segment_video_url(session, segment),
                }
                for segment in segments
            ],
        })


class CameraAiRecordingVideoView(APIView):
    """Stream a verified local segment without storing it on the web server."""

    def get_permissions(self):
        # Native <video> cannot attach the JWT stored by the SPA. Access is
        # granted by a short-lived token issued only by the protected list API.
        return [AllowAny()]

    def get(self, request, pk: int):
        session = _history_session(pk)
        try:
            token_data = signing.loads(
                request.query_params.get("token", ""),
                salt=RECORDING_TOKEN_SALT,
                max_age=RECORDING_TOKEN_MAX_AGE,
            )
            if token_data.get("session") != session.pk:
                raise signing.BadSignature("wrong session")
            requested_start = str(token_data["start"])
            requested_duration = float(token_data["duration"])
        except (signing.BadSignature, signing.SignatureExpired, KeyError, TypeError, ValueError):
            return Response({
                "detail": "Ссылка на видео недействительна или устарела",
                "code": "bad_recording_token",
            }, status=status.HTTP_403_FORBIDDEN)
        try:
            segments = _session_segments(session)
        except recordings.RecordingUnavailable:
            return Response({
                "detail": "Архив на компьютере камер сейчас недоступен",
                "code": "recording_unavailable",
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        segment = next((
            item for item in segments
            if item["start"] == requested_start
            and abs(item["duration"] - requested_duration) < 0.01
        ), None)
        if segment is None:
            raise ValidationError({"detail": "Фрагмент не найден", "code": "segment_not_found"})
        try:
            upstream = recordings.open_segment(
                _recording_stream(session), segment["start"], segment["duration"],
            )
        except recordings.RecordingUnavailable:
            return Response({
                "detail": "Не удалось открыть видео на компьютере камер",
                "code": "recording_unavailable",
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

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
        response["Content-Disposition"] = f'inline; filename="loading-{session.order_id}.mp4"'
        response["Cache-Control"] = "private, no-store"
        return response
