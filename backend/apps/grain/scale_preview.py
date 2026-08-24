"""Cached, sanitized Grain scale state for operator displays."""

from uuid import uuid4

from django.core.cache import cache, caches
from django.utils import timezone

from . import scale

PREVIEW_CACHE_KEY = "truck-scale:preview:v1"
PREVIEW_LOCK_KEY = "truck-scale:preview-lock:v1"
READY_CACHE_SECONDS = 1
OFFLINE_CACHE_SECONDS = 3
# Longer than the configured 1-second preview timeout, with enough margin for
# connection teardown. If a worker dies, the next manual refresh recovers
# after this short TTL.
LOCK_SECONDS = 5

_COMPARE_AND_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _preview_cache_key(scale_key: str) -> str:
    if scale_key == scale.DEFAULT_SCALE_KEY:
        return PREVIEW_CACHE_KEY
    return f"truck-scale:{scale_key}:preview:v1"


def _preview_lock_key(scale_key: str) -> str:
    if scale_key == scale.DEFAULT_SCALE_KEY:
        return PREVIEW_LOCK_KEY
    return f"truck-scale:{scale_key}:preview-lock:v1"


def _redis_release_owned_lock(lock_key: str, owner: str) -> bool | None:
    """Atomically release an owned lock on Django's production Redis cache.

    Local tests use Django's non-Redis cache and fall back below. Production
    must compare-and-delete atomically: an expired lock may already belong to
    another worker by the time the first scale request finishes.
    """
    backend = caches["default"]
    adapter = getattr(backend, "_cache", None)
    get_client = getattr(adapter, "get_client", None)
    serializer = getattr(adapter, "_serializer", None)
    if not callable(get_client) or serializer is None:
        return None

    key = backend.make_and_validate_key(lock_key)
    client = get_client(key, write=True)
    encoded_owner = serializer.dumps(owner)
    return bool(client.eval(_COMPARE_AND_DELETE, 1, key, encoded_owner))


def _release_owned_lock(lock_key: str, owner: str) -> None:
    released = _redis_release_owned_lock(lock_key, owner)
    if released is not None:
        return
    # A generic Django cache has no atomic compare-and-delete primitive. Let
    # its short TTL expire instead of risking deletion of a reacquired lock.


def _empty_payload(state: str, *, enabled: bool = True) -> dict:
    return {
        "state": state,
        "enabled": enabled,
        "ready": False,
        "capturable": False,
        "connected": False,
        "stable": False,
        "stale": state == "stale",
        "weight_kg": None,
        "age_seconds": None,
        "updated_at": None,
        "observed_at": timezone.now().isoformat(),
        "refresh_mode": "manual",
    }


def _serialize(observation: scale.ScaleObservation) -> dict:
    # Keep the HTTP boundary defensive even though the scale client already
    # clears unsafe readings. A stale/disconnected value must never reappear
    # because of a future caller constructing an observation incorrectly.
    weight = (
        observation.weight_kg
        if observation.state in {"ready", "unstable"}
        else None
    )
    ready = observation.state == "ready"
    return {
        "state": observation.state,
        "enabled": True,
        "ready": ready,
        # Zero is useful on the display but must never be captured as a truck.
        "capturable": bool(ready and weight is not None and weight > 0),
        "connected": observation.connected,
        "stable": observation.stable,
        "stale": observation.stale,
        "weight_kg": str(weight) if weight is not None else None,
        "age_seconds": (
            str(observation.age_seconds)
            if observation.age_seconds is not None
            else None
        ),
        "updated_at": observation.updated_at,
        "observed_at": timezone.now().isoformat(),
        "refresh_mode": "manual",
    }


def _read_uncached(scale_key: str) -> dict:
    try:
        return _serialize(scale.read_truck_scale_observation(scale_key))
    except scale.TruckScaleDisabled:
        return _empty_payload("disabled", enabled=False)
    except scale.TruckScaleMalformedResponse:
        return _empty_payload("malformed")
    except scale.TruckScaleUnavailable:
        return _empty_payload("unavailable")


def get_scale_preview(scale_key: str = scale.DEFAULT_SCALE_KEY) -> dict:
    """Return one micro-cached preview without queuing upstream requests."""
    if scale_key not in scale.SCALE_KEYS:
        raise ValueError(f"Unknown truck scale: {scale_key}")

    preview_cache_key = _preview_cache_key(scale_key)
    preview_lock_key = _preview_lock_key(scale_key)
    cached = cache.get(preview_cache_key)
    if cached is not None:
        return cached

    lock_owner = uuid4().hex
    if not cache.add(preview_lock_key, lock_owner, LOCK_SECONDS):
        # The winning worker may have filled the cache between our first read
        # and the failed lock attempt. Prefer that fresh value over a visual
        # "refreshing" flicker.
        cached = cache.get(preview_cache_key)
        if cached is not None:
            return cached
        # Another worker is already talking to the PC. A transient neutral
        # state is safer than holding another web worker on the same timeout.
        return _empty_payload("refreshing")

    try:
        payload = _read_uncached(scale_key)
        ttl = (
            READY_CACHE_SECONDS
            if payload["state"] in {"ready", "unstable", "stale"}
            else OFFLINE_CACHE_SECONDS
        )
        cache.set(preview_cache_key, payload, ttl)
        return payload
    finally:
        _release_owned_lock(preview_lock_key, lock_owner)
