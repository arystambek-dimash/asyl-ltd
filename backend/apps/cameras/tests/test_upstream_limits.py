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


def test_legacy_status_caps_each_poll_to_configured_bridge_timeout(settings):
    settings.CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS = 275
    response = UpstreamResponse(
        b'{"cam":"cam2","running":true,"mode":"session","total":4}'
    )

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        result = ai.legacy_status("cam2", timeout_seconds=10)

    assert result == {
        "cam": "cam2",
        "running": True,
        "mode": "session",
        "total": 4,
    }
    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("/processors/cam2")
    assert urlopen.call_args.kwargs["timeout"] == 0.275
    assert response.closed


def test_legacy_status_honours_a_smaller_caller_deadline(settings):
    settings.CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS = 350
    response = UpstreamResponse(b'{"running":false,"mode":"idle","total":0}')

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        ai.legacy_status("cam2", timeout_seconds=0.05)

    assert urlopen.call_args.kwargs["timeout"] == 0.05


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_legacy_status_rejects_invalid_deadlines_without_network(timeout):
    with patch("urllib.request.urlopen") as urlopen, pytest.raises(ValueError):
        ai.legacy_status("cam2", timeout_seconds=timeout)
    urlopen.assert_not_called()


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
