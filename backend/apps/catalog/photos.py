"""Фото товара: приём с нормализацией и отдача по подписанной ссылке.

MEDIA_ROOT nginx не отдаёт — файл идёт через SignedMediaView. Подпись
детерминирована и включает имя файла: ссылка стабильна, пока фото то же
(браузер кэширует), и сама устаревает после замены фото.
"""

from __future__ import annotations

import io
import uuid

from django.core import signing
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import NotFound, ValidationError

from apps.common.signed_media import SignedMediaView
from apps.eventlog.services import log_event

from .models import Product

SIGNING_SALT = "catalog.product.photo"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SOURCE_PIXELS = 50_000_000
# Карточке каталога и корзине больше не нужно; фото с телефона ужимается в разы.
MAX_SIDE_PX = 1200
UNSUPPORTED_MESSAGE = "Загрузите фото в формате JPG, PNG или WEBP"


def _normalized_jpeg(upload) -> ContentFile:
    if (getattr(upload, "size", 0) or 0) > MAX_UPLOAD_BYTES:
        raise ValidationError({"photo": "Фото больше 10 МБ"})
    try:
        with Image.open(upload) as source:
            if source.width * source.height > MAX_SOURCE_PIXELS:
                raise ValidationError({"photo": "Слишком большое разрешение фото"})
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA", "P"):
                # Прозрачный фон PNG — белый, а не чёрный после конвертации в JPEG.
                rgba = image.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")
            image.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=82, optimize=True, progressive=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValidationError({"photo": UNSUPPORTED_MESSAGE}) from exc
    return ContentFile(buffer.getvalue())


def _delete_file_after_commit(name: str) -> None:
    if not name:
        return
    storage = Product._meta.get_field("photo").storage
    transaction.on_commit(lambda: storage.delete(name))


def set_product_photo(product: Product, upload, user) -> Product:
    if upload is None:
        raise ValidationError({"photo": "Выберите фото"})
    content = _normalized_jpeg(upload)
    with transaction.atomic():
        locked = Product.objects.select_for_update().get(pk=product.pk)
        previous = locked.photo.name
        locked.photo.save(f"{uuid.uuid4().hex}.jpg", content, save=False)
        locked.save(update_fields=["photo"])
        _delete_file_after_commit(previous)
        log_event("catalog", "Фото товара обновлено", user=user, payload={"product_id": locked.pk})
    return locked


def remove_product_photo(product: Product, user) -> Product:
    with transaction.atomic():
        locked = Product.objects.select_for_update().get(pk=product.pk)
        if not locked.photo:
            return locked
        previous = locked.photo.name
        locked.photo = ""
        locked.save(update_fields=["photo"])
        _delete_file_after_commit(previous)
        log_event("catalog", "Фото товара удалено", user=user, payload={"product_id": locked.pk})
    return locked


def _signer() -> signing.Signer:
    return signing.Signer(salt=SIGNING_SALT)


def product_photo_url(product: Product) -> str | None:
    if not product.photo:
        return None
    token = _signer().sign_object({"id": product.pk, "v": product.photo.name}, compress=True)
    return f"/api/product-photos/{product.pk}/?token={token}"


class ProductPhotoView(SignedMediaView):
    """Фото товара для <img> каталога и корзины: без токена API, только по подписи."""

    def get(self, request, pk: int):
        try:
            payload = _signer().unsign_object(request.query_params.get("token", ""))
        except signing.BadSignature as exc:
            raise NotFound("Фото недоступно") from exc
        if payload.get("id") != pk:
            raise NotFound("Фото не найдено")
        product = Product.objects.filter(pk=pk).only("id", "photo").first()
        if product is None or not product.photo or product.photo.name != payload.get("v"):
            raise NotFound("Фото обновилось — обновите страницу")
        try:
            handle = product.photo.open("rb")
        except OSError as exc:
            raise NotFound("Файл фото не найден") from exc
        # Ссылка меняется вместе с файлом, поэтому кэш может жить долго.
        return self.file_response(handle, cache_control="private, max-age=31536000, immutable")
