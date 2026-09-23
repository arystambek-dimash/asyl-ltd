"""Green-API в режиме опроса: разбор уведомлений и четыре метода API без сети."""
import io
import json
import urllib.error
from datetime import UTC, datetime

import pytest

from apps.bots.providers.green_api import (
    DELETED,
    EDITED,
    MESSAGE,
    GreenApiClient,
    GreenApiError,
    GreenApiNotConfigured,
    incoming_message,
    state_change,
)

GROUP = "120363043968066561@g.us"
TOKEN = "secret-token-123"


def webhook(message_data, *, id_message="BAE5F4886F6F2D05", type_webhook="incomingMessageReceived", **extra):
    return {
        "typeWebhook": type_webhook,
        "instanceData": {"idInstance": 1101000001, "wid": "77010000000@c.us", "typeInstance": "whatsapp"},
        "timestamp": 1758268800,
        "idMessage": id_message,
        "senderData": {
            "chatId": GROUP,
            "chatName": "Отгрузка вагонов",
            "sender": "998901112233@c.us",
            "senderName": "Jin",
            "senderContactName": "Джин-Син",
        },
        "messageData": message_data,
        **extra,
    }


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Записывает запросы и отвечает заготовками по очереди."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response if isinstance(response, bytes) else json.dumps(response).encode())


def client(*responses):
    opener = FakeOpener(*responses)
    return GreenApiClient(
        api_url="https://api.green-api.com/", instance_id="1101000001", token=TOKEN, receive_timeout=20,
        opener=opener,
    ), opener


# --- уведомления -----------------------------------------------------------------------------------


def test_text_message_is_read_with_sender_and_time():
    message = incoming_message(webhook({"typeMessage": "textMessage", "textMessageData": {"textMessage": "отчёт"}}))

    assert message.kind == MESSAGE
    assert (message.message_id, message.chat_id, message.chat_name) == ("BAE5F4886F6F2D05", GROUP, "Отгрузка вагонов")
    assert (message.sender_id, message.sender_name, message.text) == ("998901112233@c.us", "Джин-Син", "отчёт")
    assert message.sent_at == datetime(2025, 9, 19, 8, 0, tzinfo=UTC)
    assert message.target_id == ""


@pytest.mark.parametrize("message_type", ["extendedTextMessage", "quotedMessage"])
def test_extended_and_quoted_text_is_read(message_type):
    body = webhook({"typeMessage": message_type, "extendedTextMessageData": {"text": "сб 19.09.26"}})

    assert incoming_message(body).text == "сб 19.09.26"


def test_edit_and_delete_point_to_the_original_message():
    edited = incoming_message(webhook({
        "typeMessage": "editedMessage",
        "editedMessageData": {"textMessage": "исправленный отчёт", "stanzaId": "ORIGINAL"},
    }, id_message="EDIT"))
    deleted = incoming_message(webhook({
        "typeMessage": "deletedMessage", "deletedMessageData": {"stanzaId": "ORIGINAL"},
    }, id_message="DELETE"))

    assert (edited.kind, edited.target_id, edited.text) == (EDITED, "ORIGINAL", "исправленный отчёт")
    assert (deleted.kind, deleted.target_id, deleted.text) == (DELETED, "ORIGINAL", "")


@pytest.mark.parametrize(
    "body",
    [
        webhook({"typeMessage": "imageMessage", "fileMessageData": {"caption": "фото отчёта"}}),
        webhook({"typeMessage": "textMessage", "textMessageData": {"textMessage": "моё"}},
                type_webhook="outgoingAPIMessageReceived"),
        {"typeWebhook": "outgoingMessageStatus", "status": "read"},
        webhook({"typeMessage": "textMessage"}, id_message=""),
        "not a dict",
        None,
    ],
)
def test_other_notifications_are_not_messages(body):
    assert incoming_message(body) is None


def test_private_chat_sender_defaults_to_chat():
    body = webhook({"typeMessage": "textMessage", "textMessageData": {"textMessage": "привет"}})
    body["senderData"] = {"chatId": "77011234567@c.us"}

    message = incoming_message(body)

    assert (message.sender_id, message.sender_name, message.chat_name) == ("77011234567@c.us", "", "")


def test_state_change_notification():
    assert state_change({"typeWebhook": "stateInstanceChanged", "stateInstance": "notAuthorized"}) == "notAuthorized"
    assert state_change({"typeWebhook": "incomingMessageReceived"}) is None


# --- методы API ------------------------------------------------------------------------------------


def test_receive_notification_long_polls_and_returns_receipt():
    api, opener = client({"receiptId": 42, "body": {"typeWebhook": "incomingMessageReceived"}})

    notification = api.receive_notification()

    assert (notification.receipt_id, notification.body) == (42, {"typeWebhook": "incomingMessageReceived"})
    request, timeout = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == (
        f"https://api.green-api.com/waInstance1101000001/receiveNotification/{TOKEN}?receiveTimeout=20")
    assert timeout == 30  # долгий опрос + запас на сеть


def test_empty_queue_is_none():
    api, _ = client(b"null")

    assert api.receive_notification() is None


def test_delete_notification_by_receipt():
    api, opener = client({"result": True})

    api.delete_notification(42)

    request, _ = opener.requests[0]
    assert request.get_method() == "DELETE"
    assert request.full_url.endswith(f"/deleteNotification/{TOKEN}/42")


def test_send_message_quotes_the_report():
    api, opener = client({"idMessage": "REPLY1"})

    assert api.send_message(GROUP, "Проведено: заказ №1", quoted_message_id="BAE5") == "REPLY1"

    request, _ = opener.requests[0]
    assert request.get_method() == "POST"
    assert request.full_url.endswith(f"/sendMessage/{TOKEN}")
    assert json.loads(request.data) == {"chatId": GROUP, "message": "Проведено: заказ №1", "quotedMessageId": "BAE5"}


def test_state_instance():
    api, _ = client({"stateInstance": "authorized"})

    assert api.get_state_instance() == "authorized"


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(f"https://x/{TOKEN}", 401, "Unauthorized", {}, None),
        urllib.error.URLError("timed out"),
        TimeoutError(),
    ],
)
def test_failures_never_leak_the_token(failure):
    api, _ = client(failure)

    with pytest.raises(GreenApiError) as raised:
        api.get_state_instance()

    assert TOKEN not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


@pytest.mark.parametrize("payload", [b"<html>", {"stateInstance": ""}, {"receiptId": "x"}])
def test_unexpected_answers_are_errors(payload):
    api, _ = client(payload, payload)

    with pytest.raises(GreenApiError):
        api.get_state_instance() if payload != {"receiptId": "x"} else api.receive_notification()


def test_missing_credentials_are_reported():
    with pytest.raises(GreenApiNotConfigured):
        GreenApiClient(api_url="https://api.green-api.com", instance_id="", token="", receive_timeout=20)
