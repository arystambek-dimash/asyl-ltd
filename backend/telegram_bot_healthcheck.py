"""Docker liveness check for the Telegram bot (run_telegram_bot).

A Telegram outage or a rejected token is ``degraded`` and stays healthy; a
stalled or exited polling loop fails.
"""

from __future__ import annotations

from apps.common.heartbeat import report_heartbeat_from_env


def main() -> int:
    return report_heartbeat_from_env(
        "TELEGRAM_BOT_HEARTBEAT_FILE",
        "/tmp/telegram-bot/heartbeat.json",
        "TELEGRAM_BOT_HEARTBEAT_MAX_AGE_SECONDS",
        180,
    )


if __name__ == "__main__":
    raise SystemExit(main())
