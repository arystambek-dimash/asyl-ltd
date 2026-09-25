"""Heartbeat фоновых процессов для healthcheck контейнера.

Файл пишется атомарно на каждом круге цикла, без записи в PostgreSQL:
healthcheck (``*_healthcheck.py`` рядом с manage.py) читает статус и
возраст — зависший или упавший цикл Docker перезапускает.

Модуль без импортов Django: healthcheck-скрипты запускаются голым
``python /app/<скрипт>.py`` и берут отсюда же проверку контракта.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Collection
from pathlib import Path

MAX_HEARTBEAT_BYTES = 4096
# Выключенный флагом или деградировавший из-за зависимости цикл жив;
# нездоров только зависший или вышедший.
LIVE_STATUSES = frozenset({"disabled", "running", "degraded"})


def write_heartbeat(path_value: str, status: str, *, now: float | None = None) -> None:
    """Publish loop liveness atomically without a database write on every poll."""
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"status": status, "updated_at": time.time() if now is None else now},
        separators=(",", ":"),
    ).encode("utf-8")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def check_heartbeat(
    path: str | Path,
    *,
    max_age_seconds: int,
    live_statuses: Collection[str] = LIVE_STATUSES,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return liveness and a non-sensitive diagnostic message."""
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
    if not isinstance(status, str) or status not in live_statuses:
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


def report_heartbeat(
    path: str | Path,
    *,
    max_age_seconds: int,
    live_statuses: Collection[str] = LIVE_STATUSES,
) -> int:
    """Exit code for Docker healthcheck; the diagnostic goes to stdout/stderr."""
    healthy, message = check_heartbeat(
        path, max_age_seconds=max_age_seconds, live_statuses=live_statuses
    )
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


def report_heartbeat_from_env(
    file_env: str,
    default_file: str,
    max_age_env: str,
    default_max_age_seconds: int,
    *,
    live_statuses: Collection[str] = LIVE_STATUSES,
) -> int:
    """:func:`report_heartbeat` с путём и возрастом из окружения контейнера."""
    return report_heartbeat(
        os.environ.get(file_env) or default_file,
        max_age_seconds=_env_positive_int(max_age_env, default_max_age_seconds),
        live_statuses=live_statuses,
    )


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default
