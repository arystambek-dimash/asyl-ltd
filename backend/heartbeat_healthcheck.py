"""Docker liveness check для фоновых циклов на apps.common.daemon.

Путь heartbeat и допустимый возраст передаются из compose аргументами:

    python /app/heartbeat_healthcheck.py /tmp/camera-monitor/heartbeat.json 300

Сбой зависимости (камеры, база) — живой ``degraded``; зависший или
вышедший цикл — нездоров. Контракт heartbeat — :mod:`apps.common.heartbeat`.
"""

from __future__ import annotations

import sys

from apps.common.heartbeat import report_heartbeat


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        path, max_age_text = args
        max_age_seconds = int(max_age_text)
    except ValueError:
        print("usage: heartbeat_healthcheck.py <heartbeat-file> <max-age-seconds>", file=sys.stderr)
        return 1
    return report_heartbeat(path, max_age_seconds=max_age_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
