"""Best-effort external alerts for confirmed camera incidents.

Alert delivery must never stop the monitor.  Destinations are optional and
configured only through environment variables so credentials never enter the
repository or incident JSON.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

WEBHOOK_URL = os.environ.get("CAMERA_ALERT_WEBHOOK_URL", "").strip()
WEBHOOK_TOKEN = os.environ.get("CAMERA_ALERT_WEBHOOK_TOKEN", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("CAMERA_ALERT_TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("CAMERA_ALERT_TELEGRAM_CHAT_ID", "").strip()
TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class Delivery:
    configured: bool
    delivered: bool
    errors: tuple[str, ...] = ()


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> None:
    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status >= 300:
            raise OSError(f"HTTP {response.status}")
        response.read(1024)


def _post_telegram(text: str) -> None:
    # application/x-www-form-urlencoded avoids assumptions about Telegram's
    # JSON parser and keeps the payload small.
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_notification": "false"}
    ).encode()
    request = urllib.request.Request(
        url,
        method="POST",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status >= 300:
            raise OSError(f"HTTP {response.status}")
        response.read(1024)


def send(event: str, payload: dict) -> Delivery:
    """Send one transition to every configured destination.

    A destination is considered delivered independently; one successful
    channel is enough to mark the transition as externally reported while the
    other channel's error remains in the audit record.
    """

    message = str(payload.get("message") or event)
    if event == "camera_outage":
        log.critical(message)
    else:
        log.warning(message)

    configured = False
    successes = 0
    errors: list[str] = []

    if WEBHOOK_URL:
        configured = True
        headers = {"Authorization": f"Bearer {WEBHOOK_TOKEN}"} if WEBHOOK_TOKEN else {}
        try:
            _post_json(WEBHOOK_URL, {"event": event, **payload}, headers)
            successes += 1
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            errors.append(f"webhook: {type(exc).__name__}")
            log.exception("Camera alert webhook delivery failed")

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        configured = True
        try:
            _post_telegram(message)
            successes += 1
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            errors.append(f"telegram: {type(exc).__name__}")
            log.exception("Camera Telegram alert delivery failed")

    if not configured:
        errors.append("no alert destination configured")
        log.error("Camera incident has no external alert destination configured")

    return Delivery(configured=configured, delivered=successes > 0, errors=tuple(errors))
