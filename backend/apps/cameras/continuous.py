from __future__ import annotations

from . import ai
from .models import MonoblockCameraSettings


def reconcile() -> dict:
    """Make the camera-PC durable state match PostgreSQL's desired state."""
    desired = sorted(MonoblockCameraSettings.always_on_sources())
    current = ai.always_on_status()
    current_sources = current.get("cameras")
    current_source = current.get("source", "sub")
    if sorted(current_sources or []) != desired or current_source != "sub":
        return ai.configure_always_on(desired, "sub")
    return current
