"""Алерт камер в Telegram — через общий клиент Bot API; сбой Telegram не роняет доставку."""
from unittest.mock import patch

import pytest

from apps.cameras import alerts
from apps.common.telegram import TelegramClient, TelegramError


@pytest.fixture
def telegram_only(monkeypatch):
    monkeypatch.setattr(alerts, "WEBHOOK_URL", "")
    monkeypatch.setattr(alerts, "TELEGRAM_BOT_TOKEN", "123:AAH-alert_token_value_12")
    monkeypatch.setattr(alerts, "TELEGRAM_CHAT_ID", "-100500")


def test_alert_goes_to_the_telegram_chat(telegram_only):
    with patch.object(TelegramClient, "send_message", return_value="-100500:1") as send:
        delivery = alerts.send("camera_outage", {"message": "Камера 3 не отвечает"})

    send.assert_called_once_with("-100500", "Камера 3 не отвечает")
    assert delivery == alerts.Delivery(delivered=True)


def test_telegram_failure_is_reported_not_raised(telegram_only):
    with patch.object(TelegramClient, "send_message", side_effect=TelegramError("Telegram sendMessage: HTTP 400")):
        delivery = alerts.send("camera_outage", {"message": "Камера 3 не отвечает"})

    assert delivery == alerts.Delivery(delivered=False, errors=("telegram: TelegramError",))


def test_malformed_token_is_a_failed_delivery_not_a_crash(telegram_only, monkeypatch):
    monkeypatch.setattr(alerts, "TELEGRAM_BOT_TOKEN", "not a token")

    delivery = alerts.send("camera_outage", {"message": "Камера 3 не отвечает"})

    assert delivery == alerts.Delivery(delivered=False, errors=("telegram: TelegramError",))
