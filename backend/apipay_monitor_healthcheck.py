"""Docker health check for the long-running ApiPay reconciliation monitor.

An iteration with retryable failures writes ``error`` and fails the check
until the next successful iteration.
"""

from __future__ import annotations

from apps.common.heartbeat import report_heartbeat_from_env


def main() -> int:
    return report_heartbeat_from_env(
        "APIPAY_MONITOR_HEARTBEAT_FILE",
        "/tmp/apipay-monitor-heartbeat",
        "APIPAY_MONITOR_HEARTBEAT_MAX_AGE_SECONDS",
        600,
        live_statuses={"running", "ok"},
    )


if __name__ == "__main__":
    raise SystemExit(main())
