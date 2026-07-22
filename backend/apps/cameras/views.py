import logging
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff, IsSuperUser
from apps.orders.models import Order
from apps.shipments.services import begin_camera_loading, finish_ai_loading

from . import ai, analytics, health, recordings, services, sessions
from .models import AiCountingSession, MonoblockCameraSettings, MonoblockDevice

log = logging.getLogger(__name__)

CAM_COOKIE = "cam_token"
CAM_TOKEN_MAX_AGE = 12 * 3600  # секунд
CAM_TOKEN_AUDIENCE = "go2rtc-websocket"
CAM_TOKEN_SALT = "cameras.stream-cookie.v2"
CAM_TOKEN_VERSION = 1
CAM_STREAM_PATH = "/go2rtc/api/ws"
MAX_ORIGINAL_URI_LENGTH = 2048
RECORDING_TOKEN_MAX_AGE = 10 * 60
RECORDING_TOKEN_SALT = "camera-recording"


def _device_for(user):
    return getattr(user, "active_monoblock_device", None)


def _camera_token_payload(user) -> dict:
    return {
        "version": CAM_TOKEN_VERSION,
        "audience": CAM_TOKEN_AUDIENCE,
        "user_id": user.pk,
        # Django's keyed session hash changes whenever the password hash does,
        # without exposing the password hash itself in the signed cookie.
        "revocation": user.get_session_auth_hash(),
    }


def _camera_token_user(token: str):
    if not isinstance(token, str) or not token or len(token) > 4096:
        return None
    try:
        payload = signing.loads(
            token,
            salt=CAM_TOKEN_SALT,
            max_age=CAM_TOKEN_MAX_AGE,
        )
    except (signing.BadSignature, TypeError, ValueError):
        return None

    expected_keys = {"version", "audience", "user_id", "revocation"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return None
    if payload["version"] != CAM_TOKEN_VERSION:
        return None
    if payload["audience"] != CAM_TOKEN_AUDIENCE:
        return None
    user_id = payload["user_id"]
    revocation = payload["revocation"]
    if type(user_id) is not int or user_id <= 0 or not isinstance(revocation, str):
        return None

    User = get_user_model()
    try:
        user = User.objects.select_related("monoblock_device").get(pk=user_id)
    except User.DoesNotExist:
        return None
    if not user.is_active or user.is_client:
        return None
    if not constant_time_compare(revocation, user.get_session_auth_hash()):
        return None
    return user


def _is_valid_camera_stream_source(source: str) -> bool:
    if not source or source.strip() != source:
        return False
    try:
        normalized_source = services.normalize_camera_path(source)
    except ValueError:
        normalized_source = None
    if normalized_source == source:
        return True
    if not source.endswith("ai"):
        return False
    base_source = source[:-2]
    try:
        return services.normalize_camera_path(base_source) == base_source
    except ValueError:
        return False


def _camera_stream_source(original_uri: str | None) -> str | None:
    if (
        not isinstance(original_uri, str)
        or not original_uri
        or len(original_uri) > MAX_ORIGINAL_URI_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in original_uri)
    ):
        return None
    try:
        parsed = urlsplit(original_uri)
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
    except ValueError:
        return None
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or parsed.path != CAM_STREAM_PATH
        or len(query) != 1
        or query[0][0] != "src"
    ):
        return None
    source = query[0][1]
    return source if _is_valid_camera_stream_source(source) else None


def _assert_device_camera(user, camera: str) -> None:
    device = _device_for(user)
    if device is not None and device.camera_source != camera:
        raise PermissionDenied("Эта камера закреплена за другим моноблоком")


class CameraListView(APIView):
    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsStaff()]
        return [HasPerm("rbac.manage")]

    def get(self, request):
        names = MonoblockCameraSettings.display_names()
        cameras = []
        for camera in services.discover_cameras():
            source = camera.get("src")
            cameras.append(
                {
                    **camera,
                    "zone": (
                        names.get(source, camera.get("zone"))
                        if isinstance(source, str)
                        else camera.get("zone")
                    ),
                }
            )
        device = _device_for(request.user)
        if device is not None:
            cameras = [
                camera
                for camera in cameras
                if camera.get("src") == device.camera_source
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
            signing.dumps(_camera_token_payload(request.user), salt=CAM_TOKEN_SALT),
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
    def _payload(settings_row=None, device=None):
        row = settings_row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        if device is not None:
            return {
                "camera_sources": [device.camera_source],
                "locked": True,
                "device_id": device.pk,
                "device_name": device.name,
                "updated_at": device.updated_at,
            }
        return {
            "camera_sources": row.camera_sources if row else [],
            "locked": False,
            "device_id": None,
            "device_name": None,
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        return Response(self._payload(device=_device_for(request.user)))

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


def _device_payload(device):
    names = MonoblockCameraSettings.display_names()
    return {
        "id": device.pk,
        "name": device.name,
        "username": device.user.username,
        "camera_source": device.camera_source,
        "camera_name": names.get(device.camera_source, device.camera_source),
        "is_active": device.is_active,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
    }


def _clean_device_data(data, *, instance=None):
    name = " ".join(str(data.get("name", getattr(instance, "name", ""))).split())
    username = " ".join(str(data.get(
        "username", getattr(getattr(instance, "user", None), "username", "")
    )).split())
    raw_camera = data.get("camera_source", getattr(instance, "camera_source", ""))
    try:
        camera = ai.normalize(raw_camera)
    except (ai.AiError, TypeError):
        raise ValidationError({"detail": "Выберите корректную камеру", "code": "bad_camera"})
    if not name or len(name) > 80:
        raise ValidationError({"detail": "Название обязательно, максимум 80 символов", "code": "bad_name"})
    if not username or len(username) > 150:
        raise ValidationError({"detail": "Логин обязателен, максимум 150 символов", "code": "bad_username"})
    users = get_user_model().objects.filter(username__iexact=username)
    if instance is not None:
        users = users.exclude(pk=instance.user_id)
    if users.exists():
        raise ValidationError({"detail": "Такой логин уже используется", "code": "username_busy"})
    devices = MonoblockDevice.objects.filter(camera_source=camera)
    if instance is not None:
        devices = devices.exclude(pk=instance.pk)
    if devices.exists():
        raise ValidationError({"detail": "Камера уже закреплена за другим моноблоком", "code": "camera_busy"})
    return name, username, camera


class MonoblockDeviceListView(APIView):
    """Суперпользователь создаёт отдельные аккаунты физических устройств."""

    permission_classes = [IsSuperUser]

    def get(self, request):
        devices = MonoblockDevice.objects.select_related("user").all()
        return Response([_device_payload(device) for device in devices])

    def post(self, request):
        name, username, camera = _clean_device_data(request.data)
        password = request.data.get("password") or ""
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": "; ".join(exc.messages), "code": "weak_password"})
        User = get_user_model()
        with transaction.atomic():
            user = User.objects.create_user(
                username=username, password=password, is_client=False,
            )
            device = MonoblockDevice.objects.create(
                user=user, name=name, camera_source=camera,
                is_active=request.data.get("is_active", True) is not False,
                created_by=request.user,
            )
        from apps.eventlog.services import log_event
        log_event(
            "monoblock_device", f"Создан моноблок «{name}»",
            user=request.user,
            payload={"device_id": device.pk, "username": username, "camera": camera},
        )
        return Response(_device_payload(device), status=status.HTTP_201_CREATED)


class MonoblockDeviceDetailView(APIView):
    permission_classes = [IsSuperUser]

    def _get(self, pk):
        return get_object_or_404(MonoblockDevice.objects.select_related("user"), pk=pk)

    def patch(self, request, pk):
        device = self._get(pk)
        name, username, camera = _clean_device_data(request.data, instance=device)
        password = request.data.get("password")
        if password:
            try:
                validate_password(password, user=device.user)
            except DjangoValidationError as exc:
                raise ValidationError({"detail": "; ".join(exc.messages), "code": "weak_password"})
        before = {
            "name": device.name, "username": device.user.username,
            "camera": device.camera_source, "is_active": device.is_active,
        }
        with transaction.atomic():
            device.name = name
            device.camera_source = camera
            if "is_active" in request.data:
                device.is_active = bool(request.data.get("is_active"))
            device.save(update_fields=["name", "camera_source", "is_active", "updated_at"])
            device.user.username = username
            device.user.is_active = device.is_active
            if password:
                device.user.set_password(password)
            device.user.save(update_fields=["username", "is_active", "password"])
        from apps.eventlog.services import log_event
        log_event(
            "monoblock_device", f"Изменён моноблок «{name}»",
            user=request.user,
            payload={"device_id": device.pk, "before": before,
                     "after": {"name": name, "username": username,
                               "camera": camera, "is_active": device.is_active}},
        )
        return Response(_device_payload(device))

    put = patch

    def delete(self, request, pk):
        device = self._get(pk)
        if AiCountingSession.objects.filter(
            camera=device.camera_source,
            status__in=AiCountingSession.OPEN_STATUSES,
        ).exists():
            raise ValidationError({
                "detail": "Сначала завершите активную отгрузку этого моноблока",
                "code": "monoblock_busy",
            })
        snapshot = _device_payload(device)
        name = device.name
        with transaction.atomic():
            device.user.delete()
        from apps.eventlog.services import log_event
        log_event(
            "monoblock_device", f"Удалён моноблок «{name}»",
            user=request.user,
            payload={
                "device_id": snapshot["id"], "name": snapshot["name"],
                "username": snapshot["username"],
                "camera": snapshot["camera_source"],
            },
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AlwaysOnCameraSettingsView(APIView):
    """Superuser-only control plane for inference-only 24/7 processors."""

    permission_classes = [IsSuperUser]

    @staticmethod
    def _payload(row=None, live=None, sync_status="synced", detail=""):
        row = row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        desired = row.always_on_camera_sources if row else []
        return {
            "camera_sources": desired,
            "source": "sub",
            "processors": (live or {}).get("processors", []),
            "capacity": (live or {}).get("capacity"),
            "service_available": live is not None,
            "sync_status": sync_status,
            "detail": detail,
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        row = MonoblockCameraSettings.objects.filter(singleton=True).first()
        if not ai.enabled():
            return Response(self._payload(
                row, sync_status="pending", detail="AI-сервис не настроен",
            ))
        try:
            live = ai.always_on_status()
            desired = row.always_on_camera_sources if row else []
            synced = sorted(live.get("cameras") or []) == sorted(desired)
            return Response(self._payload(
                row,
                live,
                "synced" if synced else "pending",
                "" if synced else "Настройка ожидает синхронизации",
            ))
        except (ai.AiUnavailable, ai.AiError) as exc:
            return Response(self._payload(
                row, sync_status="pending", detail=str(exc),
            ))

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

        row, _ = MonoblockCameraSettings.objects.get_or_create(singleton=True)
        live_before = None
        if ai.enabled():
            try:
                live_before = ai.always_on_status()
            except (ai.AiUnavailable, ai.AiError) as error:
                log.warning(
                    "Camera processor capacity is temporarily unavailable: %s",
                    error,
                )
        capacity = (live_before or {}).get("capacity")
        if isinstance(capacity, int) and len(normalized) > capacity:
            raise ValidationError({
                "camera_sources": (
                    f"ПК камер поддерживает до {capacity} активных процессоров"
                ),
                "code": "always_on_capacity_exceeded",
            })
        row.always_on_camera_sources = normalized
        row.updated_by = request.user
        row.save(update_fields=[
            "always_on_camera_sources", "updated_by", "updated_at",
        ])
        if not ai.enabled():
            return Response(self._payload(
                row, sync_status="pending", detail="AI-сервис не настроен",
            ), status=status.HTTP_202_ACCEPTED)
        try:
            live = ai.configure_always_on(normalized, "sub")
            return Response(self._payload(row, live))
        except (ai.AiUnavailable, ai.AiError) as exc:
            # PostgreSQL remains authoritative. The camera-monitor retries,
            # so a temporary camera-PC outage never loses the administrator's
            # desired configuration.
            return Response(self._payload(
                row, sync_status="pending", detail=str(exc),
            ), status=status.HTTP_202_ACCEPTED)


class AlwaysOnAnalyticsView(APIView):
    """Сегодняшний накопленный 24/7-счёт; доступен только суперпользователю."""

    permission_classes = [IsSuperUser]

    def get(self, request):
        if ai.enabled():
            try:
                analytics.record_snapshot(ai.always_on_status())
            except (ai.AiUnavailable, ai.AiError):
                # Уже сохранённая аналитика остаётся доступной при обрыве связи.
                pass
        return Response(analytics.today_payload())


class AlwaysOnAnalyticsSubtractView(APIView):
    """Аудируемое уменьшение дневного итога суперпользователем."""

    permission_classes = [IsSuperUser]

    def post(self, request, cam: str):
        return Response(analytics.subtract_today(
            cam,
            request.data.get("amount"),
            request.data.get("reason") or "",
            request.user,
        ))


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

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]
    # Subrequest'ы nginx приходят без X-Forwarded-For и делили бы один
    # anon-бакет на всех зрителей — 429 здесь гасил бы всю камерную стену.
    throttle_classes: list[type] = []

    def get(self, request):
        source = _camera_stream_source(request.META.get("HTTP_X_ORIGINAL_URI"))
        if source is None:
            return Response(status=status.HTTP_403_FORBIDDEN)
        token = request.COOKIES.get(CAM_COOKIE, "")
        user = _camera_token_user(token)
        if user is None:
            return Response(status=status.HTTP_403_FORBIDDEN)
        device = getattr(user, "monoblock_device", None)
        if device is not None and (
            not device.is_active
            or source not in {device.camera_source, f"{device.camera_source}ai"}
        ):
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
        http = (
            e.status
            if e.status in (400, 401, 404, 409, 503)
            else status.HTTP_502_BAD_GATEWAY
        )
        return Response({"detail": e.detail, "code": "ai_error"}, status=http)
    except sessions.AiSessionBusy as e:
        return _busy_response(e.session, user)


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


class CameraCountingLineView(APIView):
    """Superuser-only proxy for a camera's persisted counting line."""

    permission_classes = [IsSuperUser]

    def get(self, request, cam: str):
        return _ai_proxy_response(lambda: ai.counting_line(cam))

    def put(self, request, cam: str):
        # save_counting_line performs one PUT only. A 503 with saved=true is
        # deliberately passed to the browser without an automatic retry.
        return _ai_proxy_response(lambda: ai.save_counting_line(cam, request.data))


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
            _assert_device_camera(request.user, camera)
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
            if live.get("mode") == "always_on":
                # The Windows service may have restarted and restored its
                # durable 24/7 mode while PostgreSQL still owns an open order
                # session. Re-enter session mode on the already warm model.
                live = ai.start(camera)
            sessions.update_status(session, live)
            return {**live, **metadata}

        return _ai_response(get_status, request.user)

    def post(self, request, cam: str):
        def start():
            camera = ai.normalize(cam)
            _assert_device_camera(request.user, camera)
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
            _assert_device_camera(request.user, camera)
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
            raw_complete = request.data.get(
                "complete_order", request.query_params.get("complete_order"),
            )
            complete_order = raw_complete is True or str(raw_complete).lower() in ("1", "true")
            if complete_order:
                total = final.get("total") if isinstance(final, dict) else None
                finish_ai_loading(order, total, request.user)
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
                **({"order_status": "shipped", "bags_loaded": total}
                   if complete_order else {}),
            }
        return _ai_response(stop, request.user)


class CameraAiResetView(APIView):
    """Обнулить счётчик работающей модели — новая погрузка на той же камере."""

    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, cam: str):
        def reset():
            camera = ai.normalize(cam)
            _assert_device_camera(request.user, camera)
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
        device = _device_for(request.user)
        if device is not None:
            open_sessions = open_sessions.filter(camera=device.camera_source)
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
