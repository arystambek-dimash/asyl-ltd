import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from apps.cameras import ai


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


def test_ai_error_text_prefers_camera_pc_error_everywhere(monkeypatch):
    # cv-service пишет причину в «error»; общий _call раньше брал «detail»
    # первым, а распознавание номера и датасет — «error».
    payload = {"error": "camera is busy", "detail": "legacy detail"}
    monkeypatch.setattr(ai, "_request", lambda *_args, **_kwargs: (409, payload))
    with pytest.raises(ai.AiError) as call_error:
        ai._call("GET", "/status")
    with pytest.raises(ai.AiError) as sample_error:
        ai.clear_orientation_samples()
    assert call_error.value.detail == sample_error.value.detail == "camera is busy"
    assert call_error.value.payload == payload


def test_ai_client_imports_without_django_apps():
    # Сборщик весов (weighbridge) грузит ai.py с INSTALLED_APPS = []:
    # импорт моделей камер здесь роняет его при старте.
    result = subprocess.run(
        [sys.executable, "-c", "import apps.cameras.ai"],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.weighbridge_settings"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
