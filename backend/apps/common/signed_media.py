"""Base view for private files served through short-lived signed links."""

from typing import ClassVar

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
