"""Signed, short-lived links to grain photos: weighings and trip arrivals.

Photos live under MEDIA_ROOT, which nginx never serves directly. A serializer
issues a signed token, and the browser fetches the file through an
authentication-free view that trusts only the signature. This mirrors
``apps.tasks.attachments`` so the media directory stays private.
"""

from __future__ import annotations

from django.core import signing
from rest_framework.exceptions import NotFound

from apps.common.signed_media import SignedMediaView

from .models import UnassignedWeighing, Wagon, WeighingRecord, WeighingPhotoDelivery

SIGNING_SALT = "grain.weighing.photo"
# Long enough for an open wagon card; short enough that a leaked link expires.
PHOTO_LINK_MAX_AGE_SECONDS = 60 * 60
KIND_WEIGHING = "weighing"
KIND_UNASSIGNED = "unassigned"
KIND_EVIDENCE = "evidence"
KIND_ARRIVAL = "arrival"
# Link kind → the model and its file field.
_SOURCES = {
    KIND_WEIGHING: (WeighingRecord, "photo"),
    KIND_UNASSIGNED: (UnassignedWeighing, "photo"),
    KIND_EVIDENCE: (WeighingPhotoDelivery, "photo"),
    KIND_ARRIVAL: (Wagon, "arrival_photo"),
}


def _source(kind: str):
    try:
        return _SOURCES[kind]
    except KeyError:
        raise ValueError(f"Unknown photo kind: {kind}") from None


def photo_token(kind: str, pk: int) -> str:
    _source(kind)
    return signing.dumps({"k": kind, "id": int(pk)}, salt=SIGNING_SALT, compress=True)


def photo_url(kind: str, instance) -> str | None:
    """Return a relative API URL or ``None`` when the row has no photo."""

    _, field = _source(kind)
    if instance is None or not getattr(instance, field, None):
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
    source = _SOURCES.get(kind)
    if source is None or payload.get("k") != kind or payload.get("id") != pk:
        raise NotFound("Фото не найдено")
    model, field = source
    instance = model.objects.filter(pk=pk).first()
    photo = getattr(instance, field) if instance is not None else None
    if not photo:
        raise NotFound("Фото не найдено")
    return photo


class GrainPhotoView(SignedMediaView):
    """Serve one private grain photo through a signed link."""

    def get(self, request, kind: str, pk: int):
        photo = _photo_from_token(kind, int(pk), request.query_params.get("token", ""))
        try:
            handle = photo.open("rb")
        except OSError as exc:
            raise NotFound("Файл фото не найден") from exc
        return self.file_response(handle)
