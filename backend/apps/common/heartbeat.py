"""Heartbeat фоновых процессов для healthcheck контейнера.

Файл пишется атомарно на каждом круге цикла, без записи в PostgreSQL:
healthcheck (``*_healthcheck.py`` рядом с manage.py) читает статус и
возраст — зависший или упавший цикл Docker перезапускает.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


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
