import json
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from apps.cameras import ai, recordings


class TrackingBytesIO(BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class UpstreamResponse(TrackingBytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status


def _http_error(status: int, body: bytes) -> tuple[HTTPError, TrackingBytesIO]:
    stream = TrackingBytesIO(body)
    error = HTTPError(
        "http://camera.test/resource",
        status,
        "upstream error",
        {},
        stream,
    )
    return error, stream


def test_ai_valid_json_status_and_body_are_preserved_and_response_is_closed():
    response = UpstreamResponse(b'{"ok":true,"marker":7}', status=202)
    with patch("urllib.request.urlopen", return_value=response):
        result = ai._request("GET", "/status")

    assert result == (202, {"ok": True, "marker": 7})
    assert response.read_sizes == [ai.MAX_JSON_RESPONSE_BYTES + 1]
    assert response.closed


def test_ai_valid_error_json_status_and_body_are_preserved_and_response_is_closed():
    error, stream = _http_error(409, b'{"detail":"busy","marker":9}')
    with patch("urllib.request.urlopen", side_effect=error):
        result = ai._request("GET", "/status")

    assert result == (409, {"detail": "busy", "marker": 9})
    assert stream.read_sizes == [ai.MAX_ERROR_JSON_RESPONSE_BYTES + 1]
    assert stream.closed


def test_ai_rejects_oversized_success_json_and_closes_response():
    response = UpstreamResponse(b"x" * (ai.MAX_JSON_RESPONSE_BYTES + 1))
    with patch("urllib.request.urlopen", return_value=response), \
         pytest.raises(ai.AiUnavailable):
        ai._request("GET", "/status")

    assert response.read_sizes == [ai.MAX_JSON_RESPONSE_BYTES + 1]
    assert response.closed


def test_ai_rejects_oversized_error_json_and_closes_response():
    error, stream = _http_error(
        503,
        b"x" * (ai.MAX_ERROR_JSON_RESPONSE_BYTES + 1),
    )
    with patch("urllib.request.urlopen", side_effect=error), \
         pytest.raises(ai.AiError) as exc_info:
        ai._request("GET", "/status")

    assert exc_info.value.status == 503
    assert stream.read_sizes == [ai.MAX_ERROR_JSON_RESPONSE_BYTES + 1]
    assert stream.closed


@pytest.mark.parametrize("body", [b"{", b"[]", b"null"])
def test_ai_rejects_malformed_or_non_object_success_json(body):
    response = UpstreamResponse(body)
    with patch("urllib.request.urlopen", return_value=response), \
         pytest.raises(ai.AiUnavailable):
        ai._request("GET", "/status")
    assert response.closed


@pytest.mark.parametrize("body", [b"{", b"[]", b"null"])
def test_ai_rejects_malformed_or_non_object_error_json(body):
    error, stream = _http_error(400, body)
    with patch("urllib.request.urlopen", side_effect=error), \
         pytest.raises(ai.AiError) as exc_info:
        ai._request("GET", "/status")
    assert exc_info.value.status == 400
    assert isinstance(exc_info.value.detail, str)
    assert stream.closed


def test_ai_error_detail_must_be_a_nonempty_string(monkeypatch):
    monkeypatch.setattr(ai, "_request", lambda *_args, **_kwargs: (400, {"detail": []}))
    with pytest.raises(ai.AiError) as exc_info:
        ai._call("GET", "/status")
    assert exc_info.value.detail == "AI-сервис: ошибка 400"


@pytest.mark.parametrize("body", [
    b"x" * (recordings.MAX_SEGMENT_LIST_BYTES + 1),
    b"{",
    b"{}",
])
def test_recording_list_rejects_oversized_or_malformed_json_and_closes_response(body):
    response = UpstreamResponse(body)
    now = datetime.now(timezone.utc)
    with patch.object(recordings, "_request", return_value=response), \
         pytest.raises(recordings.RecordingUnavailable):
        recordings.list_segments("cam2ai", now, now)
    assert response.closed


def test_recording_list_caps_segment_count_and_closes_response():
    segment = {"start": "2026-07-22T10:00:00+00:00", "duration": 1}
    body = json.dumps([segment] * (recordings.MAX_SEGMENTS + 25)).encode()
    assert len(body) < recordings.MAX_SEGMENT_LIST_BYTES
    response = UpstreamResponse(body)
    now = datetime.now(timezone.utc)

    with patch.object(recordings, "_request", return_value=response):
        result = recordings.list_segments("cam2ai", now, now)

    assert len(result) == recordings.MAX_SEGMENTS
    assert response.read_sizes == [recordings.MAX_SEGMENT_LIST_BYTES + 1]
    assert response.closed


def test_recording_http_error_response_is_closed():
    error, stream = _http_error(502, b"upstream failure")
    with patch("urllib.request.urlopen", side_effect=error), \
         pytest.raises(recordings.RecordingUnavailable):
        recordings._request("/list")
    assert stream.closed
