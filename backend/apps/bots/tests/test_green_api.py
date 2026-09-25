"""Green-API в режиме опроса: разбор уведомлений и четыре метода API без сети."""
import io
import json
import socket
import urllib.error

import pytest

from apps.bots.models import BotMessage
from apps.bots.providers.green_api import (
    GreenApiClient,
    GreenApiError,
    GreenApiOutcomeUnknown,
    incoming_message,
    state_change,
)
from apps.bots.tests.whatsapp_fakes import GROUP, JIN, SENT_AT, text_webhook, webhook

TOKEN = "secret-token-123"


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
    message = incoming_message(text_webhook("отчёт"))

    assert message.kind == BotMessage.MESSAGE
    assert (message.message_id, message.chat_id, message.chat_name) == ("MSG1", GROUP, "Отгрузка вагонов")
    assert (message.sender_id, message.sender_name, message.text) == (JIN, "Джин-Син", "отчёт")
    assert message.sent_at == SENT_AT
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

    assert (edited.kind, edited.target_id, edited.text) == (BotMessage.EDITED, "ORIGINAL", "исправленный отчёт")
    assert (deleted.kind, deleted.target_id, deleted.text) == (BotMessage.DELETED, "ORIGINAL", "")


@pytest.mark.parametrize(
    "body",
    [
        webhook({"typeMessage": "imageMessage", "fileMessageData": {"caption": "фото отчёта"}}),
        text_webhook("моё", type_webhook="outgoingAPIMessageReceived"),
        {"typeWebhook": "outgoingMessageStatus", "status": "read"},
        webhook({"typeMessage": "textMessage"}, id_message=""),
        "not a dict",
        None,
    ],
)
def test_other_notifications_are_not_messages(body):
    assert incoming_message(body) is None


def test_private_chat_sender_defaults_to_chat():
    body = text_webhook("привет")
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


@pytest.mark.parametrize(
    ("method", "payload"),
    [("get_state_instance", b"<html>"), ("get_state_instance", {"stateInstance": ""}),
     ("receive_notification", {"receiptId": "x"})],
)
def test_unexpected_answers_are_errors(method, payload):
    api, _ = client(payload)

    with pytest.raises(GreenApiError):
        getattr(api, method)()


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(f"https://x/{TOKEN}", 400, "Bad Request", {}, None),
        urllib.error.HTTPError(f"https://x/{TOKEN}", 429, "Too Many Requests", {}, None),
        urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
    ],
)
def test_refused_requests_surely_did_not_send(failure):
    """Провайдер отказал (4xx) или запрос не ушёл (соединение, DNS) — отправку можно повторить."""
    api, _ = client(failure)

    with pytest.raises(GreenApiError) as raised:
        api.send_message(GROUP, "отчёт")

    assert not isinstance(raised.value, GreenApiOutcomeUnknown)


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(f"https://x/{TOKEN}", 502, "Bad Gateway", {}, None),
        urllib.error.URLError(TimeoutError("timed out")),
        urllib.error.URLError("timed out"),
        TimeoutError(),
        ConnectionResetError(),
        b"<html>",
        {"idMessage": ""},
    ],
)
def test_unanswered_requests_may_have_sent(failure):
    """Ответа нет (тайм-аут, обрыв), 5xx или непонятный ответ — сообщение могло уйти: повтор задвоил бы его."""
    api, _ = client(failure)

    with pytest.raises(GreenApiOutcomeUnknown) as raised:
        api.send_message(GROUP, "отчёт")

    assert TOKEN not in str(raised.value)


def test_missing_credentials_are_reported():
    with pytest.raises(GreenApiError, match="WHATSAPP_BOT_INSTANCE_ID"):
        GreenApiClient(api_url="https://api.green-api.com", instance_id="", token="", receive_timeout=20)
