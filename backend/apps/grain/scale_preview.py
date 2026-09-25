"""Cached, sanitized Grain scale state for operator displays."""

from uuid import uuid4

from django.core.cache import cache
from django.utils import timezone

from apps.common.locks import claim_owned_lease, release_owned_lease

from . import scale

READY_CACHE_SECONDS = 1
OFFLINE_CACHE_SECONDS = 3
# Longer than the configured 1-second preview timeout, with enough margin for
# connection teardown. If a worker dies, the next poll recovers after this
# short TTL.
LOCK_SECONDS = 5


def _preview_cache_key(scale_key: str) -> str:
    return f"truck-scale:{scale_key}:preview:v1"


def _preview_lock_key(scale_key: str) -> str:
    return f"truck-scale:{scale_key}:preview-lock:v1"


def _empty_payload(state: str, *, enabled: bool = True) -> dict:
    return {
        "state": state,
        "enabled": enabled,
        "ready": False,
        "capturable": False,
        "connected": False,
        "stable": False,
        "stale": False,
        "weight_kg": None,
        "age_seconds": None,
        "updated_at": None,
        "observed_at": timezone.now().isoformat(),
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
    if not claim_owned_lease(preview_lock_key, lock_owner, LOCK_SECONDS):
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
        # An expired lock may already belong to another worker by the time
        # this scale request finishes, so only its owner may delete it.
        release_owned_lease(preview_lock_key, lock_owner)
