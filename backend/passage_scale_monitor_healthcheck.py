"""Docker liveness check for the automatic passage-scale monitor."""

from __future__ import annotations

from apps.common.heartbeat import report_heartbeat_from_env


def main() -> int:
    return report_heartbeat_from_env(
        "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE",
        "/tmp/passage-scale-monitor/heartbeat.json",
        "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS",
        60,
    )


if __name__ == "__main__":
    raise SystemExit(main())
