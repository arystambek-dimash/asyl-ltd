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

from . import services

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
