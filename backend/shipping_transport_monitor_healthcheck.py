"""Docker liveness check for automatic shipping transport acquisition."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

DEFAULT_HEARTBEAT_FILE = "/tmp/shipping-transport-monitor/heartbeat.json"
DEFAULT_MAX_AGE_SECONDS = 180
MAX_HEARTBEAT_BYTES = 4096
LIVE_STATES = frozenset({"running", "degraded"})


def check_heartbeat(
    path: str | Path,
    *,
    max_age_seconds: int,
    now: float | None = None,
) -> tuple[bool, str]:
    """An OCR outage is degraded; a stalled or exited polling loop is unhealthy."""
    try:
        with Path(path).open("rb") as heartbeat:
            raw = heartbeat.read(MAX_HEARTBEAT_BYTES + 1)
    except OSError as exc:
        return False, f"heartbeat is unavailable: {exc}"
    if len(raw) > MAX_HEARTBEAT_BYTES:
        return False, "heartbeat exceeds its size limit"

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "heartbeat is not valid JSON"
    if not isinstance(payload, dict):
        return False, "heartbeat must be a JSON object"
    status = payload.get("status")
    if not isinstance(status, str) or status not in LIVE_STATES:
        return False, f"monitor reported invalid status={status!r}"
    updated_at = payload.get("updated_at")
    if (
        isinstance(updated_at, bool)
        or not isinstance(updated_at, (int, float))
        or not math.isfinite(updated_at)
    ):
        return False, "heartbeat timestamp is invalid"

    checked_at = time.time() if now is None else now
    age = checked_at - updated_at
    if age < -60:
        return False, "heartbeat timestamp is unexpectedly in the future"
    if age > max(1, max_age_seconds):
        return False, f"heartbeat is stale ({int(age)}s)"
    return True, f"monitor status={status}, heartbeat age={max(0, int(age))}s"


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


def main() -> int:
    healthy, message = check_heartbeat(
        os.environ.get("SHIPPING_TRANSPORT_HEARTBEAT_FILE") or DEFAULT_HEARTBEAT_FILE,
        max_age_seconds=_env_positive_int(
            "SHIPPING_TRANSPORT_HEARTBEAT_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS
        ),
    )
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
