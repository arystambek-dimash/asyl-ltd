"""Docker health check for the long-running ApiPay reconciliation monitor."""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

DEFAULT_HEARTBEAT_FILE = "/tmp/apipay-monitor-heartbeat"
DEFAULT_MAX_AGE_SECONDS = 600
HEALTHY_STATES = frozenset({"running", "ok"})


def check_heartbeat(
    path: str | Path,
    *,
    max_age_seconds: int,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return health and a non-sensitive diagnostic message."""

    heartbeat = Path(path)
    try:
        fields = heartbeat.read_text(encoding="utf-8").strip().split()
    except OSError as exc:
        return False, f"heartbeat is unavailable: {exc}"

    if len(fields) != 2:
        return False, "heartbeat has an invalid format"

    state, timestamp_text = fields
    if state not in HEALTHY_STATES:
        return False, f"monitor reported state={state}"

    try:
        timestamp = float(timestamp_text)
    except ValueError:
        return False, "heartbeat timestamp is invalid"
    if not math.isfinite(timestamp):
        return False, "heartbeat timestamp is invalid"

    checked_at = time.time() if now is None else now
    age = checked_at - timestamp
    if age < -60:
        return False, "heartbeat timestamp is unexpectedly in the future"
    if age > max(1, max_age_seconds):
        return False, f"heartbeat is stale ({int(age)}s)"
    return True, f"monitor state={state}, heartbeat age={max(0, int(age))}s"


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


def main() -> int:
    healthy, message = check_heartbeat(
        os.environ.get(
            "APIPAY_MONITOR_HEARTBEAT_FILE",
            DEFAULT_HEARTBEAT_FILE,
        ),
        max_age_seconds=_env_positive_int(
            "APIPAY_MONITOR_HEARTBEAT_MAX_AGE_SECONDS",
            DEFAULT_MAX_AGE_SECONDS,
        ),
    )
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
