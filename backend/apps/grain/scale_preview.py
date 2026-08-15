"""Cached, sanitized Grain scale state for operator displays."""

from django.core.cache import cache
from django.utils import timezone

from . import scale

PREVIEW_CACHE_KEY = "truck-scale:preview:v1"
PREVIEW_LOCK_KEY = "truck-scale:preview-lock:v1"
READY_CACHE_SECONDS = 1
OFFLINE_CACHE_SECONDS = 3
# Longer than the configured 1-second preview timeout, with enough margin for
# connection teardown. If a worker dies, polling recovers after this short TTL.
LOCK_SECONDS = 5


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
        "poll_after_ms": 5000,
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
        "poll_after_ms": (
            2000
            if observation.state in {"ready", "unstable", "stale"}
            else 5000
        ),
    }


def _read_uncached() -> dict:
    try:
        return _serialize(scale.read_truck_scale_observation())
    except scale.TruckScaleDisabled:
        return _empty_payload("disabled", enabled=False)
    except scale.TruckScaleMalformedResponse:
        return _empty_payload("malformed")
    except scale.TruckScaleUnavailable:
        return _empty_payload("unavailable")


def get_scale_preview() -> dict:
    """Return one micro-cached preview without queuing upstream requests."""
    cached = cache.get(PREVIEW_CACHE_KEY)
    if cached is not None:
        return cached

    if not cache.add(PREVIEW_LOCK_KEY, "1", LOCK_SECONDS):
        # The winning worker may have filled the cache between our first read
        # and the failed lock attempt. Prefer that fresh value over a visual
        # "refreshing" flicker.
        cached = cache.get(PREVIEW_CACHE_KEY)
        if cached is not None:
            return cached
        # Another worker is already talking to the PC. A transient neutral
        # state is safer than holding another web worker on the same timeout.
        refreshing = _empty_payload("refreshing")
        refreshing["poll_after_ms"] = 1000
        return refreshing

    try:
        payload = _read_uncached()
        ttl = (
            READY_CACHE_SECONDS
            if payload["state"] in {"ready", "unstable", "stale"}
            else OFFLINE_CACHE_SECONDS
        )
        cache.set(PREVIEW_CACHE_KEY, payload, ttl)
        return payload
    finally:
        cache.delete(PREVIEW_LOCK_KEY)
