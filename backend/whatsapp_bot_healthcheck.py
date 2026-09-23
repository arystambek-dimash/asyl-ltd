"""Docker liveness check for the WhatsApp bot (run_whatsapp_bot).

A provider outage or an unauthorized number is ``degraded`` and stays
healthy; a stalled or exited polling loop fails. The heartbeat contract is
shared with the passage-scale monitor.
"""

from __future__ import annotations

import os
import sys

from passage_scale_monitor_healthcheck import _env_positive_int, check_heartbeat

DEFAULT_HEARTBEAT_FILE = "/tmp/whatsapp-bot/heartbeat.json"
DEFAULT_MAX_AGE_SECONDS = 180


def main() -> int:
    healthy, message = check_heartbeat(
        os.environ.get("WHATSAPP_BOT_HEARTBEAT_FILE") or DEFAULT_HEARTBEAT_FILE,
        max_age_seconds=_env_positive_int(
            "WHATSAPP_BOT_HEARTBEAT_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS
        ),
    )
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
