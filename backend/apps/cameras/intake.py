"""«Приход сегодня» на «Главной»: мешки камер AI 24/7 за календарный день по товарам.

Цвет мешка переводится в товар той же настройкой «Куда приходовать»
(:class:`AlwaysOnColorProductMapping`), по которой идёт автоприход. Цвет без
товара (не определён, не настроен) остаётся строкой с цветом и без веса.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.catalog.photos import product_photo_url

from .analytics import today_payload
from .models import ANALYTICS_SCOPE_AI247, AlwaysOnColorProductMapping


def _camera_items(camera: str, colors: list[dict], mappings: dict) -> tuple[list[dict], Decimal]:
    by_product: dict[int, dict] = {}
    unmapped: list[dict] = []
    kg = Decimal(0)
    for row in colors:
        bags = row["total"]
        if bags <= 0:
            continue
        product = mappings.get((camera, row["color"].lower()))
        if product is None:
            unmapped.append({"product": None, "color": row["color"], "bags": bags})
            continue
        kg += product.weight_kg * bags
        item = by_product.setdefault(
            product.pk,
            {
                "product": product.pk,
                "name": product.name,
                "weight_kg": str(product.weight_kg),
                "color_label": product.get_color_display(),
                "photo_url": product_photo_url(product),
                "bags": 0,
            },
        )
        item["bags"] += bags
    items = sorted(by_product.values(), key=lambda item: -item["bags"]) + sorted(
        unmapped, key=lambda item: -item["bags"]
    )
    return items, kg


def today_intake_payload() -> dict:
    """Сегодняшний приход камер AI 24/7: всего, вес по фасовке и товары каждой камеры."""

    day = timezone.localdate()
    analytics = today_payload(ANALYTICS_SCOPE_AI247, date_from=day, date_to=day)
    mappings = {
        (row.camera, row.color): row.product
        for row in AlwaysOnColorProductMapping.objects.filter(
            camera__in=[camera["camera"] for camera in analytics["cameras"]]
        ).select_related("product")
    }
    cameras = []
    total_kg = Decimal(0)
    for camera in analytics["cameras"]:
        items, kg = _camera_items(camera["camera"], camera["colors"], mappings)
        total_kg += kg
        cameras.append(
            {
                "camera": camera["camera"],
                "name": camera["name"],
                "total": camera["total"],
                "items": items,
            }
        )
    return {
        "day": analytics["day"],
        "analytics_sync": analytics["analytics_sync"],
        "total": analytics["total"],
        "kg": str(total_kg),
        "cameras": cameras,
    }
