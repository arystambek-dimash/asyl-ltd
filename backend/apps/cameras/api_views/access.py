from typing import ClassVar
from urllib.parse import parse_qsl, urlsplit

from apps.common.permissions import IsStaff
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils.crypto import constant_time_compare
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import services

CAM_COOKIE = "cam_token"
CAM_TOKEN_MAX_AGE = 12 * 3600  # секунд
CAM_TOKEN_AUDIENCE = "go2rtc-websocket"
CAM_TOKEN_SALT = "cameras.stream-cookie.v2"
CAM_TOKEN_VERSION = 1
CAM_STREAM_PATH = "/go2rtc/api/ws"
MAX_ORIGINAL_URI_LENGTH = 2048


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


class CameraTokenView(APIView):
    permission_classes: ClassVar[list[type]] = [IsStaff]

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


class CameraAuthView(APIView):
    """Internal-эндпоинт для nginx auth_request — только проверка cookie."""

    authentication_classes: ClassVar[list[type]] = []
    permission_classes: ClassVar[list[type]] = [AllowAny]
    # Subrequest'ы nginx приходят без X-Forwarded-For и делили бы один
    # anon-бакет на всех зрителей — 429 здесь гасил бы всю камерную стену.
    throttle_classes: ClassVar[list[type]] = []

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
