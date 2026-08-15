import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass

from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .credentials import TOKEN_RE, digest_token
from .models import ConveyorDevice

DEVICE_CREDENTIAL_RE = re.compile(
    rb"^Device ([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.([A-Za-z0-9_-]{43})$"
)


@dataclass(frozen=True)
class ServicePrincipal:
    kind: str
    identifier: str

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False


class ConveyorDeviceAuthentication(BaseAuthentication):
    """Authenticate a high-entropy per-device bearer without storing it."""

    def authenticate(self, request):
        raw = get_authorization_header(request)
        match = DEVICE_CREDENTIAL_RE.fullmatch(raw)
        if match is None:
            raise AuthenticationFailed("Invalid device credential")
        try:
            public_id = uuid.UUID(match.group(1).decode("ascii"))
            token = match.group(2).decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:  # pragma: no cover
            raise AuthenticationFailed("Invalid device credential") from exc
        if not TOKEN_RE.fullmatch(token):  # pragma: no cover - regex invariant
            raise AuthenticationFailed("Invalid device credential")
        device = ConveyorDevice.objects.filter(public_id=public_id).first()
        if (
            device is None
            or not device.is_active
            or not hmac.compare_digest(device.secret_sha256, digest_token(token))
        ):
            raise AuthenticationFailed("Invalid device credential")
        return (
            ServicePrincipal("conveyor-device", str(device.public_id)),
            device,
        )

    def authenticate_header(self, request):
        return "Device"


class AiCallbackAuthentication(BaseAuthentication):
    """Authenticate the camera-PC's OFF-only observation callback."""

    def authenticate(self, request):
        raw = get_authorization_header(request)
        parts = raw.split(b" ", 1)
        if len(parts) != 2 or parts[0] != b"Bearer" or not parts[1]:
            raise AuthenticationFailed("Invalid callback credential")
        try:
            token = parts[1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise AuthenticationFailed("Invalid callback credential") from exc
        expected = getattr(settings, "CONVEYOR_AI_CALLBACK_TOKEN_SHA256", "")
        supplied = hashlib.sha256(token.encode("ascii")).hexdigest()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", expected)
            or not hmac.compare_digest(expected, supplied)
        ):
            raise AuthenticationFailed("Invalid callback credential")
        return ServicePrincipal("camera-ai", "callback"), "camera-ai"

    def authenticate_header(self, request):
        return "Bearer"
