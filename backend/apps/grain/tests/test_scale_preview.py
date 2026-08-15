from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.grain import scale, scale_preview

pytestmark = pytest.mark.django_db

READING_URL = "/api/truck-scale/reading/"
SAFE_RESPONSE_FIELDS = {
    "state",
    "enabled",
    "ready",
    "capturable",
    "connected",
    "stable",
    "stale",
    "weight_kg",
    "age_seconds",
    "updated_at",
    "observed_at",
    "poll_after_ms",
}


@pytest.fixture(autouse=True)
def clear_scale_preview_cache():
    cache.delete_many([
        scale_preview.PREVIEW_CACHE_KEY,
        scale_preview.PREVIEW_LOCK_KEY,
    ])
    yield
    cache.delete_many([
        scale_preview.PREVIEW_CACHE_KEY,
        scale_preview.PREVIEW_LOCK_KEY,
    ])


def observation(**overrides):
    values = {
        "state": "ready",
        "weight_kg": Decimal("3660.00"),
        "connected": True,
        "stable": True,
        "stale": False,
        "age_seconds": Decimal("0.2"),
        "updated_at": "2026-08-12T12:20:02+05:00",
    }
    values.update(overrides)
    return scale.ScaleObservation(**values)


def test_reading_endpoint_allows_grain_weigher(api_client, user_with_perms):
    user = user_with_perms(
        "preview-grain-weigher",
        codes=["grain.weigh"],
    )
    api_client.force_authenticate(user)

    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        return_value=observation(),
    ) as read_scale:
        response = api_client.get(READING_URL)

    assert response.status_code == 200
    assert set(response.data) == SAFE_RESPONSE_FIELDS
    assert response.data["state"] == "ready"
    assert response.data["weight_kg"] == "3660.00"
    assert response.data["capturable"] is True
    assert response.data["poll_after_ms"] == 2000
    assert "no-store" in response["Cache-Control"]
    read_scale.assert_called_once_with()


@pytest.mark.parametrize(
    "permission_code",
    ["shipping.arrive", "shipping.load", "shipping.ship"],
)
def test_reading_endpoint_denies_shipping_permissions_before_scale_io(
    api_client, user_with_perms, permission_code
):
    user = user_with_perms(
        f"preview-denied-{permission_code.replace('.', '-')}",
        codes=[permission_code],
    )
    api_client.force_authenticate(user)

    with patch("apps.grain.views.get_scale_preview") as get_scale_preview:
        response = api_client.get(READING_URL)

    assert response.status_code == 403
    get_scale_preview.assert_not_called()


def test_reading_endpoint_denies_unrelated_permission_before_scale_io(
    api_client, user_with_perms
):
    user = user_with_perms("preview-denied", codes=["grain.view"])
    api_client.force_authenticate(user)

    with patch(
        "apps.grain.views.get_scale_preview"
    ) as get_scale_preview:
        response = api_client.get(READING_URL)

    assert response.status_code == 403
    get_scale_preview.assert_not_called()


def test_reading_endpoint_denies_client_accounts_before_scale_io(
    api_client, client_user
):
    api_client.force_authenticate(client_user)

    with patch(
        "apps.grain.views.get_scale_preview"
    ) as get_scale_preview:
        response = api_client.get(READING_URL)

    assert response.status_code == 403
    get_scale_preview.assert_not_called()


def test_preview_is_micro_cached_across_operator_requests():
    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        return_value=observation(),
    ) as read_scale:
        first = scale_preview.get_scale_preview()
        second = scale_preview.get_scale_preview()

    assert first == second
    assert first["weight_kg"] == "3660.00"
    read_scale.assert_called_once_with()


def test_preview_single_flight_does_not_queue_another_scale_request():
    assert cache.add(scale_preview.PREVIEW_LOCK_KEY, "busy", 30) is True

    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
    ) as read_scale:
        payload = scale_preview.get_scale_preview()

    assert payload["state"] == "refreshing"
    assert payload["weight_kg"] is None
    assert payload["poll_after_ms"] == 1000
    assert cache.get(scale_preview.PREVIEW_CACHE_KEY) is None
    read_scale.assert_not_called()


@pytest.mark.parametrize(
    ("exception", "state", "enabled"),
    [
        (scale.TruckScaleDisabled(), "disabled", False),
        (scale.TruckScaleUnavailable(), "unavailable", True),
        (scale.TruckScaleMalformedResponse(), "malformed", True),
    ],
)
def test_operational_failures_return_safe_pollable_state(
    exception, state, enabled
):
    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        side_effect=exception,
    ):
        payload = scale_preview.get_scale_preview()

    assert set(payload) == SAFE_RESPONSE_FIELDS
    assert payload["state"] == state
    assert payload["enabled"] is enabled
    assert payload["weight_kg"] is None
    assert payload["ready"] is False
    assert payload["capturable"] is False
    assert payload["poll_after_ms"] == 5000


def test_zero_weight_is_visible_but_not_capturable():
    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        return_value=observation(weight_kg=Decimal("0.00")),
    ):
        payload = scale_preview.get_scale_preview()

    assert payload["state"] == "ready"
    assert payload["ready"] is True
    assert payload["weight_kg"] == "0.00"
    assert payload["capturable"] is False


@pytest.mark.parametrize("state", ["stale", "disconnected", "unavailable"])
def test_preview_never_serializes_weight_from_an_unsafe_state(state):
    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        return_value=observation(state=state, weight_kg=Decimal("9876.54")),
    ):
        payload = scale_preview.get_scale_preview()

    assert payload["state"] == state
    assert payload["weight_kg"] is None
    assert payload["capturable"] is False
