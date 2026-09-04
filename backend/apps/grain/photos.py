"""Signed, short-lived links to weighing photos.

Photos live under MEDIA_ROOT, which nginx never serves directly. A serializer
issues a signed token, and the browser fetches the file through an
authentication-free view that trusts only the signature. This mirrors
``apps.tasks.attachments`` so the media directory stays private.
"""

from __future__ import annotations

from django.core import signing
from django.http import FileResponse
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .models import UnassignedWeighing, WeighingRecord

SIGNING_SALT = "grain.weighing.photo"
# Long enough for an open wagon card; short enough that a leaked link expires.
PHOTO_LINK_MAX_AGE_SECONDS = 60 * 60
KIND_WEIGHING = "weighing"
KIND_UNASSIGNED = "unassigned"
_MODELS = {KIND_WEIGHING: WeighingRecord, KIND_UNASSIGNED: UnassignedWeighing}


def photo_token(kind: str, pk: int) -> str:
    if kind not in _MODELS:
        raise ValueError(f"Unknown photo kind: {kind}")
    return signing.dumps({"k": kind, "id": int(pk)}, salt=SIGNING_SALT, compress=True)


def photo_url(kind: str, instance) -> str | None:
    """Return a relative API URL or ``None`` when the row has no photo."""

    if instance is None or not getattr(instance, "photo", None):
        return None
    return f"/api/grain/photos/{kind}/{instance.pk}/?token={photo_token(kind, instance.pk)}"


def _photo_from_token(kind: str, pk: int, token: str):
    try:
        payload = signing.loads(
            token,
            salt=SIGNING_SALT,
            max_age=PHOTO_LINK_MAX_AGE_SECONDS,
        )
    except signing.BadSignature as exc:
        raise NotFound("Фото недоступно или ссылка устарела") from exc
    if payload.get("k") != kind or payload.get("id") != pk:
        raise NotFound("Фото не найдено")
    model = _MODELS.get(kind)
    if model is None:
        raise NotFound("Фото не найдено")
    instance = model.objects.filter(pk=pk).first()
    if instance is None or not instance.photo:
        raise NotFound("Фото не найдено")
    return instance


class WeighingPhotoView(APIView):
    """Serve one private weighing photo through a signed link."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, kind: str, pk: int):
        instance = _photo_from_token(kind, int(pk), request.query_params.get("token", ""))
        try:
            handle = instance.photo.open("rb")
        except OSError as exc:
            raise NotFound("Файл фото не найден") from exc
        response = FileResponse(handle, content_type="image/jpeg")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
