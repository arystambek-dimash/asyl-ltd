"""Docker health check for the Celery beat process."""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_PIDFILE = "/tmp/celerybeat/celerybeat.pid"


def check_pidfile(path: str | Path) -> tuple[bool, str]:
    pidfile = Path(path)
    try:
        raw_pid = pidfile.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return False, f"beat pidfile is unavailable: {exc}"

    try:
        pid = int(raw_pid)
    except ValueError:
        return False, "beat pidfile is invalid"
    if pid <= 0:
        return False, "beat pidfile is invalid"

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, f"beat process {pid} is not running"
    except PermissionError:
        # The pid exists but belongs to another user. This is still sufficient
        # for liveness; production runs beat and its health check as app.
        pass
    return True, f"beat process {pid} is running"


def main() -> int:
    healthy, message = check_pidfile(
        os.environ.get("CELERY_BEAT_PID_FILE", DEFAULT_PIDFILE)
    )
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
