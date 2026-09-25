from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

log = logging.getLogger(__name__)

WEBHOOK_URL = settings.CAMERA_ALERT_WEBHOOK_URL
WEBHOOK_TOKEN = settings.CAMERA_ALERT_WEBHOOK_TOKEN
TELEGRAM_BOT_TOKEN = settings.CAMERA_ALERT_TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.CAMERA_ALERT_TELEGRAM_CHAT_ID
TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class Delivery:
    delivered: bool
    errors: tuple[str, ...] = ()


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, method="POST", data=data, headers=headers)
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
        headers = {"Content-Type": "application/json"}
        if WEBHOOK_TOKEN:
            headers["Authorization"] = f"Bearer {WEBHOOK_TOKEN}"
        body = json.dumps({"event": event, **payload}, ensure_ascii=False).encode()
        try:
            _post(WEBHOOK_URL, body, headers)
            successes += 1
        except OSError as exc:
            errors.append(f"webhook: {type(exc).__name__}")
            log.exception("Camera alert webhook delivery failed")

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        configured = True
        # application/x-www-form-urlencoded avoids assumptions about Telegram's
        # JSON parser and keeps the payload small.
        body = urllib.parse.urlencode(
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_notification": "false",
            }
        ).encode()
        try:
            _post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                body,
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
            successes += 1
        except OSError as exc:
            errors.append(f"telegram: {type(exc).__name__}")
            log.exception("Camera Telegram alert delivery failed")

    if not configured:
        errors.append("no alert destination configured")
        log.error("Camera incident has no external alert destination configured")

    return Delivery(delivered=successes > 0, errors=tuple(errors))
