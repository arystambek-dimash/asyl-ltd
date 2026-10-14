from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cameras.models import AlwaysOnColorProductMapping, AlwaysOnCounterCursor, AlwaysOnDailyAnalytics
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def test_intake_is_today_by_product_with_weight(auth_client, admin_user, user_with_perms, ai247_camera):
    today = timezone.localdate()
    # Вчерашние мешки в «Приход сегодня» не попадают — раньше цвета брались за всю историю.
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3", day=today - timedelta(days=1), model_total=900, model_per_color={"red": 900}
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=today,
        model_total=20,
        model_per_color={"red": 10, "green": 6, "unknown": 3, "white": 1},
    )
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=timezone.now(),
    )
    first = Product.objects.create(name="Первый сорт DIKHAN 50кг", color="Red", weight_kg="50")
    second = Product.objects.create(name="Второй сорт KOROL 25кг", color="Green", weight_kg="25")
    AlwaysOnColorProductMapping.objects.create(camera="cam3", color="red", product=first)
    AlwaysOnColorProductMapping.objects.create(camera="cam3", color="green", product=second)

    denied = auth_client(user_with_perms("no-cams", codes=["dashboard.view"])).get("/api/cameras/always-on-intake/")
    assert denied.status_code == 403

    response = auth_client(admin_user).get("/api/cameras/always-on-intake/")

    assert response.status_code == 200
    assert response.data["analytics_sync"]["available"] is True
    assert response.data["total"] == 20
    assert response.data["kg"] == "650.00"  # 10 × 50 + 6 × 25; без товара — без веса
    [camera] = response.data["cameras"]
    assert camera["camera"] == "cam3"
    assert camera["name"]  # подпись камеры из настроек, без неё — код
    assert camera["total"] == 20
    assert camera["items"] == [
        {
            "product": first.pk,
            "name": "Первый сорт DIKHAN 50кг",
            "weight_kg": "50.00",
            "color_label": "Красный",
            "photo_url": None,
            "bags": 10,
        },
        {
            "product": second.pk,
            "name": "Второй сорт KOROL 25кг",
            "weight_kg": "25.00",
            "color_label": "Зелёный",
            "photo_url": None,
            "bags": 6,
        },
        {"product": None, "color": "unknown", "bags": 3},
        {"product": None, "color": "white", "bags": 1},
    ]
