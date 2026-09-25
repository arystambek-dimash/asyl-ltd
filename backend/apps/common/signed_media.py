"""Base view for private files served through short-lived signed links."""

from typing import ClassVar

from django.http import FileResponse
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView


class SignedMediaView(APIView):
    """Serve one private file to an ``<img>`` or download that carries a signed URL.

    Image tags and downloads cannot attach the API bearer token, so the
    signature — checked before any database work — is the only gate: there is
    no session to authenticate and no user to throttle by. The API-wide
    anonymous throttle would put every screen behind the plant's single public
    address into one bucket, and a page of photos answered 429 within seconds.
    """

    authentication_classes: ClassVar[list] = []
    permission_classes: ClassVar[list] = [AllowAny]
    throttle_classes: ClassVar[list] = []

    @staticmethod
    def file_response(
        handle,
        *,
        content_type: str = "image/jpeg",
        cache_control: str = "private, no-store",
        **options,
    ) -> FileResponse:
        """Stream an opened private file; the browser must not sniff its type."""
        response = FileResponse(handle, content_type=content_type, **options)
        response["Cache-Control"] = cache_control
        response["X-Content-Type-Options"] = "nosniff"
        return response
