"""Обновления Telegram для бота: текст, правка, отправитель и его username."""
from datetime import UTC, datetime

import pytest

from apps.bots.models import BotMessage
from apps.bots.providers.telegram import incoming_message, normalize_username
from apps.bots.tests.telegram_fakes import GROUP, JIN, JIN_ID, SENT_AT, update

# --- обновления -------------------------------------------------------------------------------------


def test_text_message_is_read_with_sender_username_and_time():
    message = incoming_message(update("отчёт"))

    assert message.kind == BotMessage.MESSAGE
    assert (message.message_id, message.chat_id, message.chat_name, message.chat_type) == (
        "11", GROUP, "Отгрузка вагонов", "supergroup")
    assert (message.sender_id, message.sender_name, message.sender_username) == (JIN_ID, "Джин-Син", JIN)
    assert (message.text, message.sent_at, message.is_private) == ("отчёт", SENT_AT, False)


def test_username_is_compared_without_case():
    assert incoming_message(update("x", username="Jin_Sin")).sender_username == "jin_sin"


def test_edit_keeps_the_message_id_and_has_its_own_time():
    edited_at = datetime(2026, 9, 19, 9, 40, tzinfo=UTC)
    message = incoming_message(update("исправленный отчёт", edited_at=edited_at))

    assert (message.kind, message.message_id, message.text, message.edited_at) == (
        BotMessage.EDITED, "11", "исправленный отчёт", edited_at)


def test_photo_caption_is_read_as_text():
    body = update("")
    body["message"].pop("text")
    body["message"]["caption"] = "сб 19.09.26"

    assert incoming_message(body).text == "сб 19.09.26"


@pytest.mark.parametrize("change", [
    lambda message: message.pop("text"),
    lambda message: message["from"].update(is_bot=True),
    lambda message: message.pop("from"),
    lambda message: message.update(message_id="11"),
])
def test_non_text_bot_and_malformed_messages_are_skipped(change):
    body = update("отчёт")
    change(body["message"])

    assert incoming_message(body) is None


def test_private_chat_name_is_the_person():
    message = incoming_message(update("/start", chat_id=501, chat_type="private"))

    assert (message.chat_id, message.chat_name, message.is_private) == ("501", "Джин-Син", True)


@pytest.mark.parametrize("value", ["@Dinara_K", "dinara_k", " https://t.me/dinara_k ", "t.me/Dinara_K"])
def test_username_is_normalized(value):
    assert normalize_username(value) == "dinara_k"
