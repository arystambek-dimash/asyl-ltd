"""Docker liveness check for the WhatsApp bot (run_whatsapp_bot).

A provider outage or an unauthorized number is ``degraded`` and stays
healthy; a stalled or exited polling loop fails.
"""

from __future__ import annotations

from apps.common.heartbeat import report_heartbeat_from_env


def main() -> int:
    return report_heartbeat_from_env(
        "WHATSAPP_BOT_HEARTBEAT_FILE",
        "/tmp/whatsapp-bot/heartbeat.json",
        "WHATSAPP_BOT_HEARTBEAT_MAX_AGE_SECONDS",
        180,
    )


if __name__ == "__main__":
    raise SystemExit(main())
