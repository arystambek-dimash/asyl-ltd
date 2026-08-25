from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from apps.grain import scale, scale_preview
from django.core.cache import cache

pytestmark = pytest.mark.django_db

READING_URL = "/api/truck-scale/reading/"
WAGON_READING_URL = "/api/truck-scales/wagon/reading/"
TRUCK_READING_URL = "/api/truck-scales/truck/reading/"
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
    "refresh_mode",
}


@pytest.fixture(autouse=True)
def clear_scale_preview_cache():
    keys = [
        scale_preview._preview_cache_key(scale_key)
        for scale_key in scale.SCALE_KEYS
    ] + [
        scale_preview._preview_lock_key(scale_key)
        for scale_key in scale.SCALE_KEYS
    ]
    cache.delete_many(keys)
    yield
    cache.delete_many(keys)


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
    assert response.data["refresh_mode"] == "manual"
    assert "no-store" in response["Cache-Control"]
    read_scale.assert_called_once_with(scale.TRUCK_SCALE_KEY)


@pytest.mark.parametrize("url", [TRUCK_READING_URL, READING_URL])
def test_truck_preview_routes_remain_compatible(
    api_client, user_with_perms, url
):
    user = user_with_perms(
        f"preview-truck-{url.count('/')}",
        codes=["grain.weigh"],
    )
    api_client.force_authenticate(user)

    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        return_value=observation(weight_kg=Decimal("12000.00")),
    ) as read_scale:
        response = api_client.get(url)

    assert response.status_code == 200
    assert response.data["weight_kg"] == "12000.00"
    read_scale.assert_called_once_with(scale.TRUCK_SCALE_KEY)


def test_wagon_preview_uses_only_wagon_scale(
    api_client, user_with_perms
):
    user = user_with_perms("preview-wagon", codes=["grain.weigh"])
    api_client.force_authenticate(user)

    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        return_value=observation(weight_kg=Decimal("68000.00")),
    ) as read_scale:
        response = api_client.get(WAGON_READING_URL)

    assert response.status_code == 200
    assert response.data["weight_kg"] == "68000.00"
    read_scale.assert_called_once_with(scale.WAGON_SCALE_KEY)


def test_unknown_scale_preview_is_404_before_scale_io(
    api_client, user_with_perms
):
    user = user_with_perms("preview-unknown", codes=["grain.weigh"])
    api_client.force_authenticate(user)

    with patch("apps.grain.views.get_scale_preview") as get_scale_preview:
        response = api_client.get("/api/truck-scales/unknown/reading/")

    assert response.status_code == 404
    get_scale_preview.assert_not_called()


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
    read_scale.assert_called_once_with(scale.TRUCK_SCALE_KEY)


def test_preview_caches_are_isolated_by_physical_scale():
    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        side_effect=[
            observation(weight_kg=Decimal("68000.00")),
            observation(weight_kg=Decimal("12000.00")),
        ],
    ) as read_scale:
        wagon = scale_preview.get_scale_preview(scale.WAGON_SCALE_KEY)
        truck = scale_preview.get_scale_preview(scale.TRUCK_SCALE_KEY)
        wagon_again = scale_preview.get_scale_preview(
            scale.WAGON_SCALE_KEY
        )
        truck_again = scale_preview.get_scale_preview(
            scale.TRUCK_SCALE_KEY
        )

    assert wagon["weight_kg"] == wagon_again["weight_kg"] == "68000.00"
    assert truck["weight_kg"] == truck_again["weight_kg"] == "12000.00"
    assert read_scale.call_args_list == [
        call(scale.WAGON_SCALE_KEY),
        call(scale.TRUCK_SCALE_KEY),
    ]


def test_truck_preview_lock_does_not_block_wagon_refresh():
    truck_lock = scale_preview._preview_lock_key(
        scale.TRUCK_SCALE_KEY
    )
    assert cache.add(truck_lock, "busy", 30) is True

    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
        return_value=observation(weight_kg=Decimal("68000.00")),
    ) as read_scale:
        wagon = scale_preview.get_scale_preview(scale.WAGON_SCALE_KEY)
        truck = scale_preview.get_scale_preview(scale.TRUCK_SCALE_KEY)

    assert wagon["state"] == "ready"
    assert wagon["weight_kg"] == "68000.00"
    assert truck["state"] == "refreshing"
    read_scale.assert_called_once_with(scale.WAGON_SCALE_KEY)


def test_expired_preview_owner_cannot_delete_a_reacquired_lock():
    lock_key = scale_preview._preview_lock_key(scale.TRUCK_SCALE_KEY)
    cache.set(lock_key, "new-owner", 30)

    scale_preview._release_owned_lock(lock_key, "expired-owner")

    assert cache.get(lock_key) == "new-owner"


def test_non_redis_preview_lock_is_left_to_expire_safely():
    lock_key = scale_preview._preview_lock_key(scale.WAGON_SCALE_KEY)
    cache.set(lock_key, "owner", 30)

    scale_preview._release_owned_lock(lock_key, "owner")

    assert cache.get(lock_key) == "owner"


def test_redis_preview_lock_release_is_an_atomic_owner_check():
    client = Mock()
    client.eval.return_value = 0
    serializer = Mock()
    serializer.dumps.return_value = b"encoded-owner"
    adapter = SimpleNamespace(
        get_client=Mock(return_value=client),
        _serializer=serializer,
    )
    backend = SimpleNamespace(
        _cache=adapter,
        make_and_validate_key=Mock(return_value=":1:preview-lock"),
    )

    with patch(
        "apps.grain.scale_preview.caches", {"default": backend}
    ):
        released = scale_preview._redis_release_owned_lock(
            "preview-lock", "owner"
        )

    assert released is False
    assert client.eval.call_args.args[1:] == (
        1,
        ":1:preview-lock",
        b"encoded-owner",
    )


def test_preview_single_flight_does_not_queue_another_scale_request():
    assert cache.add(scale_preview.PREVIEW_LOCK_KEY, "busy", 30) is True

    with patch.object(
        scale_preview.scale,
        "read_truck_scale_observation",
    ) as read_scale:
        payload = scale_preview.get_scale_preview()

    assert payload["state"] == "refreshing"
    assert payload["weight_kg"] is None
    assert payload["refresh_mode"] == "manual"
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
def test_operational_failures_return_safe_manual_refresh_state(
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
    assert payload["refresh_mode"] == "manual"


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
