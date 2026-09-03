"""Docker liveness check for the automatic passage-scale monitor."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

DEFAULT_HEARTBEAT_FILE = "/tmp/passage-scale-monitor/heartbeat.json"
DEFAULT_MAX_AGE_SECONDS = 60
MAX_HEARTBEAT_BYTES = 4096
LIVE_STATES = frozenset({"disabled", "running", "degraded"})


def check_heartbeat(
    path: str | Path,
    *,
    max_age_seconds: int,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return process liveness without treating hardware outages as fatal."""

    heartbeat = Path(path)
    try:
        raw = heartbeat.read_bytes()
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
    if status not in LIVE_STATES:
        return False, f"monitor reported invalid status={status!r}"

    updated_at = payload.get("updated_at")
    if (
        isinstance(updated_at, bool)
        or not isinstance(updated_at, (int, float))
        or not math.isfinite(float(updated_at))
    ):
        return False, "heartbeat timestamp is invalid"

    checked_at = time.time() if now is None else now
    age = checked_at - float(updated_at)
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
        os.environ.get(
            "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE",
            DEFAULT_HEARTBEAT_FILE,
        ),
        max_age_seconds=_env_positive_int(
            "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS",
            DEFAULT_MAX_AGE_SECONDS,
        ),
    )
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
