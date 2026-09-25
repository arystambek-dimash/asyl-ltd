import json
from unittest.mock import Mock, patch

import pytest

from apps.common import openai_responses
from apps.common.openai_responses import OpenAIResponseError, request_json

BODY = {"model": "gpt-test", "store": False, "input": "текст"}


def completed(text, *, response_id="resp_test"):
    return {
        "status": "completed",
        "id": response_id,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
    }


def connection(payload=None, *, status=200, raw=None):
    response = Mock(status=status)
    response.read.return_value = raw if raw is not None else json.dumps(payload).encode()
    client = Mock()
    client.getresponse.return_value = response
    return client


@pytest.fixture(autouse=True)
def api_key(settings):
    settings.OPENAI_API_KEY = "unit-test-only"


def test_returns_model_json_and_safe_response_id():
    client = connection(completed('{"number": "123ABC02"}'))
    with patch.object(openai_responses.http.client, "HTTPSConnection", return_value=client) as https:
        assert request_json(BODY) == ({"number": "123ABC02"}, "resp_test")
    https.assert_called_once_with("api.openai.com", timeout=45)
    call = client.request.call_args
    assert call.args == ("POST", "/v1/responses")
    assert json.loads(call.kwargs["body"]) == BODY
    assert call.kwargs["headers"]["Authorization"] == "Bearer unit-test-only"
    client.close.assert_called_once()


def test_response_limit_is_per_caller():
    client = connection(raw=b" " * 65 + json.dumps(completed("{}")).encode())
    with patch.object(openai_responses.http.client, "HTTPSConnection", return_value=client):
        with pytest.raises(OpenAIResponseError) as caught:
            request_json(BODY, max_response_bytes=64)
    assert caught.value.code == "openai_invalid_response"
    client.getresponse.return_value.read.assert_called_once_with(65)


def test_network_failure_is_retryable_without_details():
    client = connection()
    client.request.side_effect = TimeoutError("private network detail")
    with patch.object(openai_responses.http.client, "HTTPSConnection", return_value=client):
        with pytest.raises(OpenAIResponseError) as caught:
            request_json(BODY)
    assert (caught.value.code, caught.value.retryable) == ("openai_unavailable", True)
    assert "private" not in str(caught.value)
    client.close.assert_called_once()


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "{}"}, {"type": "output_text", "text": "{}"},
        ]}]},
        completed("не json"),
        ["not", "an", "object"],
    ],
)
def test_ambiguous_or_malformed_answer_is_rejected(payload):
    with patch.object(openai_responses.http.client, "HTTPSConnection", return_value=connection(payload)):
        with pytest.raises(OpenAIResponseError) as caught:
            request_json(BODY)
    assert (caught.value.code, caught.value.retryable) == ("openai_invalid_response", False)


def test_unsafe_response_id_is_dropped():
    payload = completed("{}", response_id="resp id; DROP")
    with patch.object(openai_responses.http.client, "HTTPSConnection", return_value=connection(payload)):
        assert request_json(BODY) == ({}, "")
