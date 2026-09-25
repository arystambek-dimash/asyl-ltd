"""Docker liveness check for the shipping session monitor.

An OCR outage is degraded; a stalled or exited polling loop is unhealthy.
The monitor has no disabled state, so a ``disabled`` heartbeat fails.
"""

from __future__ import annotations

from apps.common.heartbeat import report_heartbeat_from_env


def main() -> int:
    return report_heartbeat_from_env(
        "SHIPPING_TRANSPORT_HEARTBEAT_FILE",
        "/tmp/shipping-transport-monitor/heartbeat.json",
        "SHIPPING_TRANSPORT_HEARTBEAT_MAX_AGE_SECONDS",
        180,
        live_statuses={"running", "degraded"},
    )


if __name__ == "__main__":
    raise SystemExit(main())
