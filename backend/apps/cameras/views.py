"""Камеры цеха: список для дашборда и доступ к видеопотокам go2rtc.

Видео идёт мимо Django (nginx → go2rtc), поэтому доступ к потокам
защищён подписанной cookie: `token` ставит её сотруднику, `auth`
проверяет по субзапросу nginx auth_request.
"""
from django.core import signing
from django.core.signing import TimestampSigner
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPerm

from . import ai, services

CAM_COOKIE = "cam_token"
CAM_TOKEN_MAX_AGE = 12 * 3600  # секунд
_signer = TimestampSigner(salt="cameras")


class IsStaffUser(BasePermission):
    """Авторизованный сотрудник (не клиент портала)."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and not getattr(u, "is_client", False))


class CameraListView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        return Response(services.discover_cameras())


class CameraTokenView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

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


def _ai_response(fn):
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


class CameraAiView(APIView):
    """AI-подсчёт мешков: статус, включение и выключение модели на камере.

    `cam` — путь камеры у ai_service/MediaMTX: cam2 (канал NVR) или
    cam_8c26 (direct-камера, хвост MAC).
    """

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [IsAuthenticated(), IsStaffUser()]
        return [HasPerm("shipping.load")]

    def get(self, request, cam: str):
        return _ai_response(lambda: ai.status(cam) or {"running": False})

    def post(self, request, cam: str):
        def start():
            current = ai.status(cam)
            if current and current.get("running"):
                return current  # уже считает (второй планшет) — счёт не сбрасываем
            return ai.start(cam)
        return _ai_response(start)

    def delete(self, request, cam: str):
        def stop():
            final = ai.stop(cam)
            return {**(final or {}), "running": False}
        return _ai_response(stop)


class CameraAiResetView(APIView):
    """Обнулить счётчик работающей модели — новая погрузка на той же камере."""

    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, cam: str):
        return _ai_response(lambda: ai.reset(cam))
