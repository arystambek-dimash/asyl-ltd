"""Stdlib-only Docker health check for the legacy conveyor monitor.

The monitor's former health check ran through ``manage.py``.  That imported
the complete Django/Celery/Sentry stack on every probe and could exceed
Docker's timeout while a release was starting, even though the heartbeat was
fresh.  Keep this module dependency-free so the probe remains cheap and
deterministic under startup load.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import uuid
from pathlib import Path

HEARTBEAT_VERSION = 1
DEFAULT_HEARTBEAT_FILE = "/tmp/legacy-conveyor-monitor-heartbeat"
DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 5.0


def check_heartbeat(
    path: str | Path,
    *,
    max_age_seconds: float = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
    now: float | None = None,
    check_process: bool = True,
) -> tuple[bool, str]:
    """Validate the monitor heartbeat without importing or querying Django."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, f"heartbeat is unavailable: {exc}"
    if not isinstance(payload, dict):
        return False, "heartbeat has an invalid format"
    version = payload.get("version")
    if type(version) is not int or version != HEARTBEAT_VERSION:
        return False, "heartbeat has an unsupported version"
    if payload.get("state") != "ok":
        return False, f"monitor reported state={payload.get('state')}"

    timestamp = payload.get("timestamp")
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(float(timestamp))
    ):
        return False, "heartbeat timestamp is invalid"
    checked_at = time.time() if now is None else now
    age = checked_at - float(timestamp)
    if age < -60:
        return False, "heartbeat timestamp is unexpectedly in the future"
    if age > max(0.1, float(max_age_seconds)):
        return False, f"heartbeat is stale ({age:.1f}s)"

    pid = payload.get("pid")
    db_pid = payload.get("db_backend_pid")
    if type(pid) is not int or pid <= 0:
        return False, "heartbeat process id is invalid"
    if type(db_pid) is not int or db_pid <= 0:
        return False, "heartbeat database process id is invalid"
    try:
        parsed_boot_id = uuid.UUID(str(payload.get("boot_id")))
    except (AttributeError, TypeError, ValueError):
        return False, "heartbeat boot id is invalid"
    if str(parsed_boot_id) != payload.get("boot_id"):
        return False, "heartbeat boot id is invalid"

    if check_process:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, "monitor process is not running"
        except PermissionError:
            pass
        except OSError as exc:
            return False, f"monitor process check failed: {exc}"
    return True, f"monitor heartbeat is healthy ({max(0.0, age):.1f}s)"


def _max_age_from_env() -> float:
    raw = os.environ.get(
        "CONVEYOR_LEGACY_BRIDGE_HEARTBEAT_MAX_AGE_SECONDS",
        str(DEFAULT_HEARTBEAT_MAX_AGE_SECONDS),
    )
    try:
        return max(0.1, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_MAX_AGE_SECONDS


def main() -> int:
    healthy, message = check_heartbeat(
        os.environ.get(
            "CONVEYOR_LEGACY_BRIDGE_HEARTBEAT_FILE",
            DEFAULT_HEARTBEAT_FILE,
        ),
        max_age_seconds=_max_age_from_env(),
    )
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
