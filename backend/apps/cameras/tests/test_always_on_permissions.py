from unittest.mock import patch

import pytest

from apps.cameras import ai
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    MonoblockDevice,
)

pytestmark = pytest.mark.django_db


READ_ENDPOINTS = (
    "/api/cameras/always-on-settings/",
    "/api/cameras/always-on-detections/",
    "/api/cameras/always-on-analytics/",
    "/api/cameras/always-on-analytics/archives/",
    "/api/cameras/always-on-production/?camera=cam3",
)
SHIPPING_READ_ENDPOINTS = (
    "/api/cameras/shipping-continuous-settings/",
    "/api/cameras/shipping-continuous-detections/",
    "/api/cameras/shipping-continuous-analytics/",
)


@pytest.fixture(autouse=True)
def disable_camera_pc(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "")


@pytest.fixture
def read_ai_status():
    MonoblockCameraSettings.objects.update_or_create(
        singleton=True,
        defaults={"always_on_camera_sources": ["cam3"]},
    )
    ContinuousCameraRole.objects.update_or_create(
        camera="cam3",
        defaults={"analytics_scope": ANALYTICS_SCOPE_AI247},
    )
    with patch.object(
        ai,
        "always_on_detections_cached",
        return_value={"processors": []},
    ):
        yield


@pytest.mark.parametrize("permission", ["shipping.load", "ai_247.manage"])
def test_human_with_read_or_manage_permission_can_get_all_ai_247_monitoring(
    auth_client,
    user_with_perms,
    read_ai_status,
    permission,
):
    user = user_with_perms(f"ai-reader-{permission}", codes=[permission])

    for endpoint in READ_ENDPOINTS:
        assert auth_client(user).get(endpoint).status_code == 200


def test_ai_247_monitoring_get_denies_unprivileged_client_and_anonymous_users(
    api_client,
    auth_client,
    user_with_perms,
    client_user,
    read_ai_status,
):
    unprivileged = user_with_perms("ai-outsider", codes=["shipping.view"])

    for endpoint in READ_ENDPOINTS:
        assert auth_client(unprivileged).get(endpoint).status_code == 403
        assert auth_client(client_user).get(endpoint).status_code == 403
        assert api_client.get(endpoint).status_code == 401


def test_technical_monoblock_account_cannot_get_ai_247_monitoring(
    auth_client,
    make_user,
    read_ai_status,
):
    user = make_user(username="ai-technical-monoblock")
    MonoblockDevice.objects.create(
        user=user,
        name="Технический моноблок",
        camera_source="cam9",
    )
    assert user.has_perm_code("shipping.load") is True

    for endpoint in READ_ENDPOINTS:
        assert auth_client(user).get(endpoint).status_code == 403


def test_shipping_continuous_endpoints_are_separate_and_device_scoped(
    auth_client,
    make_user,
    user_with_perms,
    read_ai_status,
):
    shipping_user = user_with_perms(
        "shipping-continuous-reader",
        codes=["shipping.view"],
    )
    ai247_only = user_with_perms(
        "ai247-only-reader",
        codes=["ai_247.manage"],
    )
    device_user = make_user(username="shipping-continuous-device")
    MonoblockDevice.objects.create(
        user=device_user,
        name="Моноблок cam9",
        camera_source="cam9",
    )

    for endpoint in SHIPPING_READ_ENDPOINTS:
        assert auth_client(shipping_user).get(endpoint).status_code == 200
        assert auth_client(ai247_only).get(endpoint).status_code == 403
        response = auth_client(device_user).get(endpoint)
        assert response.status_code == 200
        if endpoint.endswith("settings/"):
            assert response.data["camera_sources"] == ["cam9"]
        elif endpoint.endswith("analytics/"):
            assert [row["camera"] for row in response.data["cameras"]] == [
                "cam9"
            ]


MUTATION_REQUESTS = (
    ("put", "/api/cameras/always-on-settings/", {"camera_sources": []}),
    (
        "post",
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 1, "color": "red", "reason": "Проверка доступа"},
    ),
    (
        "post",
        "/api/cameras/always-on-analytics/cam3/archive/",
        {"note": "Проверка доступа"},
    ),
    ("delete", "/api/cameras/always-on-analytics/archives/999/", None),
    ("put", "/api/cameras/always-on-production/", {"camera": "cam3", "mappings": []}),
    ("patch", "/api/cameras/always-on-production/", {"camera": "cam3", "mappings": []}),
    ("post", "/api/cameras/always-on-production/batches/999/retry/", {}),
)


@pytest.mark.parametrize("method,endpoint,payload", MUTATION_REQUESTS)
def test_shipping_loader_cannot_mutate_ai_247(
    auth_client,
    user_with_perms,
    method,
    endpoint,
    payload,
):
    loader = user_with_perms(
        f"ai-mutation-loader-{method}-{len(endpoint)}",
        codes=["shipping.load"],
    )
    request = getattr(auth_client(loader), method)

    response = request(endpoint, payload, format="json")

    assert response.status_code == 403


def test_ai_247_manager_can_change_settings_without_shipping_load(
    auth_client,
    user_with_perms,
):
    manager = user_with_perms("ai-settings-manager", codes=["ai_247.manage"])

    response = auth_client(manager).put(
        "/api/cameras/always-on-settings/",
        {"camera_sources": ["cam3"]},
        format="json",
    )

    assert response.status_code == 202
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.always_on_camera_sources == ["cam3"]
    assert row.updated_by == manager
