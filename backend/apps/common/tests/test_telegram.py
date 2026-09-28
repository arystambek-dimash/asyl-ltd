"""Telegram Bot API без сети: long polling, отправка, ошибки без токена, деление длинного текста."""
import http.client
import io
import json
import socket
import urllib.error

import pytest

from apps.common.telegram import (
    MESSAGE_MAX_LENGTH,
    TelegramClient,
    TelegramError,
    TelegramOutcomeUnknown,
    TelegramRefused,
    TelegramUnauthorized,
    split_text,
)

GROUP = "-1001234567890"


def update(text, *, update_id):
    return {"update_id": update_id, "message": {"message_id": 1, "text": text}}


TOKEN = "123456:AAH-secret_token_value_1"


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


def ok(result):
    return {"ok": True, "result": result}


def http_error(code, description=""):
    body = io.BytesIO(json.dumps({"ok": False, "error_code": code, "description": description}).encode())
    return urllib.error.HTTPError("https://api.telegram.org/bot…", code, "error", {}, body)


def client(*responses):
    opener = FakeOpener(*responses)
    return TelegramClient(
        token=TOKEN, token_name="TELEGRAM_BOT_TOKEN", api_url="https://api.telegram.org/", poll_timeout=25,
        opener=opener,
    ), opener


def body_of(request):
    return json.loads(request.data)


def test_long_text_is_split_by_lines():
    lines = [f"Д1с-{index:08d}-68 тн" for index in range(400)]

    parts = split_text("\n".join(lines))

    assert len(parts) > 1
    assert all(len(part) <= MESSAGE_MAX_LENGTH for part in parts)
    assert "\n".join(parts).split("\n") == lines


def test_split_cuts_a_line_longer_than_the_limit():
    assert split_text("x" * 11 + "\nтекст", limit=5) == ["xxxxx", "xxxxx", "x", "текст"]


# --- API ---------------------------------------------------------------------------------------------


def test_get_updates_long_polls_with_offset_and_only_messages():
    api, opener = client(ok([update("отчёт", update_id=5), {"update_id": "bad"}]))

    updates = api.get_updates(4)

    assert [item.update_id for item in updates] == [5]
    request, timeout = opener.requests[0]
    assert request.full_url == f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    assert body_of(request) == {"timeout": 25, "allowed_updates": ["message", "edited_message"], "offset": 4}
    assert timeout == 35


def test_send_message_replies_to_the_original_and_returns_its_ref():
    api, opener = client(ok({"message_id": 99}))

    ref = api.send_message(GROUP, "Проведено", reply_to=f"{GROUP}:11")

    assert ref == f"{GROUP}:99"
    assert body_of(opener.requests[0][0]) == {
        "chat_id": GROUP, "text": "Проведено", "link_preview_options": {"is_disabled": True},
        "reply_parameters": {"message_id": 11, "allow_sending_without_reply": True},
    }


def test_get_me_returns_the_bot_username():
    api, _ = client(ok({"id": 1, "is_bot": True, "username": "asyl_bot"}))

    assert api.get_me() == "asyl_bot"


def test_rejected_token_is_unauthorized_and_the_token_is_not_in_the_error():
    api, _ = client(http_error(401, "Unauthorized"))

    with pytest.raises(TelegramUnauthorized) as caught:
        api.get_me()

    assert "HTTP 401" in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_client_error_is_a_definite_failure_with_the_description():
    api, _ = client(http_error(400, "Bad Request: chat not found"))

    with pytest.raises(TelegramError) as caught:
        api.send_message("1", "x")

    assert isinstance(caught.value, TelegramRefused)
    assert "chat not found" in str(caught.value)


@pytest.mark.parametrize("failure", [
    http_error(502),
    urllib.error.URLError(TimeoutError("timed out")),
    b"not json",
    {"ok": True, "result": {}},
])
def test_send_without_a_clear_answer_may_have_been_delivered(failure):
    api, _ = client(failure)

    with pytest.raises(TelegramOutcomeUnknown):
        api.send_message("1", "x")


@pytest.mark.parametrize("reason", [ConnectionRefusedError(), socket.gaierror()])
def test_request_that_never_left_is_a_definite_failure(reason):
    api, _ = client(urllib.error.URLError(reason))

    with pytest.raises(TelegramError) as caught:
        api.send_message("1", "x")

    assert not isinstance(caught.value, TelegramOutcomeUnknown)


def test_missing_token_is_an_error_naming_its_setting():
    with pytest.raises(TelegramError, match="CAMERA_ALERT_TELEGRAM_BOT_TOKEN"):
        TelegramClient(token="", token_name="CAMERA_ALERT_TELEGRAM_BOT_TOKEN")


@pytest.mark.parametrize("token", ["123:short", "123456:AAH-secret token_value_1", "AAH-secret_token_value_1"])
def test_malformed_token_is_refused_without_showing_it(token):
    with pytest.raises(TelegramError) as caught:
        TelegramClient(token=token, token_name="TELEGRAM_BOT_TOKEN")

    assert "TELEGRAM_BOT_TOKEN" in str(caught.value)
    assert token not in str(caught.value)


def test_http_client_errors_never_carry_the_url_with_the_token():
    # http.client кладёт путь запроса (с токеном) в текст InvalidURL и подобных ошибок.
    api, _ = client(http.client.InvalidURL(f"URL can't contain control characters. '/bot{TOKEN}/getMe'"))

    with pytest.raises(TelegramOutcomeUnknown) as caught:
        api.get_me()

    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is not None
