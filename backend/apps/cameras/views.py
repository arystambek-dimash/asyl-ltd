from django.core import signing
from django.core.signing import TimestampSigner
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff
from apps.orders.models import Order
from apps.rbac.scoping import scope_by_department

from . import ai, health, services, sessions
from .models import AiCountingSession, MonoblockCameraSettings

CAM_COOKIE = "cam_token"
CAM_TOKEN_MAX_AGE = 12 * 3600  # секунд
_signer = TimestampSigner(salt="cameras")


class CameraListView(APIView):
    permission_classes = [IsStaff]

    def get(self, request):
        return Response(services.discover_cameras())


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
    visible = scope_by_department(
        Order.objects.all(), request.user, "shipping.load",
        owner_field="client__manager",
    )
    return get_object_or_404(visible, pk=order_id)


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


class CameraAiView(APIView):
    """AI-подсчёт мешков: статус, включение и выключение модели на камере.

    `cam` — путь камеры у ai_service/MediaMTX: cam2 (канал NVR) или
    cam_8c26 (direct-камера, хвост MAC).
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
            if live is None:
                # The worker disappeared/restarted: release the stale slot so
                # this or another order can start again immediately.
                sessions.fail(session, "AI processor stopped unexpectedly")
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
            if order.status not in ("arrived", "loading"):
                raise ai.AiError(400, "Заказ не находится на этапе погрузки")
            if order.loading_camera != camera:
                raise ai.AiError(
                    400,
                    "Камера не закреплена за заказом через Моноблок",
                )

            session, created = sessions.reserve(order, camera, request.user)

            try:
                # A new reservation calls POST directly: ai_service already
                # makes it idempotent, so the old preliminary GET only added a
                # network round-trip (and up to a 10 second delay).
                live = ai.start(camera) if created else ai.status(camera)
                if live is None:
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
            final = ai.stop(camera)
            sessions.finish(session, request.user, final)
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
        return [HasPerm("shipping.load")]

    def get(self, request):
        visible_orders = scope_by_department(
            Order.objects.all(), request.user, "shipping.load",
            owner_field="client__manager",
        )
        open_sessions = (
            AiCountingSession.objects
            .filter(
                status__in=AiCountingSession.OPEN_STATUSES,
                order__in=visible_orders,
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
