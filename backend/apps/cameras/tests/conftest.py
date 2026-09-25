import pytest


@pytest.fixture
def ai247_camera(db):
    """Камера ``cam3`` выбрана под AI 24/7 и закреплена за этим контуром."""
    from apps.cameras.models import (
        ANALYTICS_SCOPE_AI247,
        ContinuousCameraRole,
        MonoblockCameraSettings,
    )

    MonoblockCameraSettings.objects.update_or_create(
        singleton=True,
        defaults={"always_on_camera_sources": ["cam3"]},
    )
    ContinuousCameraRole.objects.update_or_create(
        camera="cam3",
        defaults={"analytics_scope": ANALYTICS_SCOPE_AI247},
    )
    return "cam3"
