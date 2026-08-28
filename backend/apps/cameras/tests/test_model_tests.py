"""Superuser model-test proxy: permissions, validation and bounded streaming."""

import json
from io import BytesIO
from unittest.mock import patch
from uuid import UUID

import pytest

from apps.cameras import ai

pytestmark = pytest.mark.django_db

JOB_ID = "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19"
INFO = {
    "enabled": True,
    "bundles": [
        {
            "id": "production",
            "ready": True,
            "detector": "detector.pt",
            "color_classifier": "color_classifier.pt",
            "brand_classifier": "brand_classifier.pt",
        }
    ],
    "defaults": {
        "line": "0,0.5,1,0.5",
        "direction": "any",
        "inference_fps": 12,
    },
    "limits": {"max_upload_bytes": 900_000_000},
    "device": "cpu",
    "reject_while_processors_active": True,
    "active_processors": 0,
    "writes_production_analytics": False,
}


@pytest.fixture(autouse=True)
def ai_key(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "backend-only-key")


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="model-root",
        password="pass12345",
    )


def _video_post(api_client, body=b"video", **extra):
    return api_client.post(
        "/api/cameras/model-tests/?bundle=production&line=0,0.5,1,0.5&direction=any&inference_fps=12",
        body,
        content_type="video/mp4",
        **extra,
    )


def test_model_test_routes_require_superuser(api_client, make_user, superuser):
    with (
        patch.object(ai, "model_test_info") as info,
        patch.object(ai, "upload_model_test") as upload,
        patch.object(ai, "model_test_status") as detail,
    ):
        assert api_client.get("/api/cameras/model-tests/").status_code == 401

        api_client.force_authenticate(make_user("ordinary-staff"))
        assert api_client.get("/api/cameras/model-tests/").status_code == 403
        assert _video_post(api_client).status_code == 403
        assert api_client.get(f"/api/cameras/model-tests/{JOB_ID}/").status_code == 403

    info.assert_not_called()
    upload.assert_not_called()
    detail.assert_not_called()


def test_info_is_proxied_without_key_and_upload_limit_is_clamped(
    api_client, superuser, settings
):
    settings.AI_MODEL_TEST_MAX_UPLOAD_BYTES = 512_000_000
    api_client.force_authenticate(superuser)
    with patch.object(ai, "model_test_info", return_value=(200, INFO)):
        response = api_client.get("/api/cameras/model-tests/")

    assert response.status_code == 200
    assert response.data["limits"]["max_upload_bytes"] == 512_000_000
    assert response.data["bundles"][0]["brand_classifier"] == "brand_classifier.pt"
    assert "backend-only-key" not in json.dumps(response.data)
    assert response.headers["Cache-Control"] == "no-store"


def test_info_strips_model_paths_before_returning_them(api_client, superuser):
    api_client.force_authenticate(superuser)
    unsafe = {
        **INFO,
        "bundles": [
            {
                **INFO["bundles"][0],
                "detector": r"C:\\models\\detector.pt",
                "brand_classifier": "/srv/models/brand_classifier.pt",
            }
        ],
    }
    with patch.object(ai, "model_test_info", return_value=(200, unsafe)):
        response = api_client.get("/api/cameras/model-tests/")
    assert response.status_code == 200
    assert response.data["bundles"][0]["detector"] == "detector.pt"
    assert response.data["bundles"][0]["brand_classifier"] == "brand_classifier.pt"


def test_raw_video_and_validated_query_are_streamed_once(api_client, superuser):
    api_client.force_authenticate(superuser)
    accepted = {
        "job_id": JOB_ID,
        "status": "queued",
        "status_url": f"/model-tests/{JOB_ID}",
        "bundle": "production",
    }
    with patch.object(ai, "upload_model_test", return_value=(202, accepted)) as upload:
        response = _video_post(api_client, b"0123456789")

    assert response.status_code == 202
    assert response.data["status_url"] == f"/api/cameras/model-tests/{JOB_ID}/"
    kwargs = upload.call_args.kwargs
    assert kwargs["content_length"] == 10
    assert kwargs["content_type"] == "video/mp4"
    assert kwargs["query"] == {
        "bundle": "production",
        "line": "0,0.5,1,0.5",
        "direction": "any",
        "inference_fps": 12.0,
    }
    assert upload.call_args.args[0].read(10) == b"0123456789"


@pytest.mark.parametrize(
    ("url", "expected_detail"),
    [
        (
            "/api/cameras/model-tests/?bundle=production&bundle=other",
            "Параметры нельзя повторять",
        ),
        (
            "/api/cameras/model-tests/?bundle=production&threshold=0.4",
            "Неизвестные параметры",
        ),
        (
            "/api/cameras/model-tests/?bundle=../model",
            "Выберите доступный набор моделей",
        ),
        (
            "/api/cameras/model-tests/?bundle=production&line=0,0,0,0",
            "не должны совпадать",
        ),
        (
            "/api/cameras/model-tests/?bundle=production&inference_fps=NaN",
            "конечным числом",
        ),
    ],
)
def test_upload_query_is_strict(api_client, superuser, url, expected_detail):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "upload_model_test") as upload:
        response = api_client.post(url, b"video", content_type="video/mp4")
    assert response.status_code == 400
    assert expected_detail in response.data["detail"]
    assert response.headers["Cache-Control"] == "no-store"
    upload.assert_not_called()


def test_upload_rejects_unsupported_or_oversized_body(api_client, superuser, settings):
    settings.AI_MODEL_TEST_MAX_UPLOAD_BYTES = 4
    api_client.force_authenticate(superuser)
    with patch.object(ai, "upload_model_test") as upload:
        oversized = _video_post(api_client, b"12345")
        unsupported = api_client.post(
            "/api/cameras/model-tests/?bundle=production",
            b"abc",
            content_type="text/plain",
        )
    assert oversized.status_code == 413
    assert oversized.data["max_upload_bytes"] == 4
    assert unsupported.status_code == 415
    upload.assert_not_called()


def test_status_cursor_is_validated_and_forwarded(api_client, superuser):
    api_client.force_authenticate(superuser)
    payload = {
        "job_id": JOB_ID,
        "status": "running",
        "config": {
            "line": "0,0.5,1,0.5",
            "direction": "any",
            "inference_fps": 12,
            "device": "cpu",
        },
        "progress": {"decoded_frames": 40, "processed_frames": 20, "percent": 50},
        "events": [],
        "page": {
            "after_event": 10,
            "limit": 500,
            "next_after_event": 10,
            "has_more": False,
            "total_events": 0,
        },
    }
    with patch.object(ai, "model_test_status", return_value=(200, payload)) as call:
        response = api_client.get(
            f"/api/cameras/model-tests/{JOB_ID}/?after_event=10&limit=500"
        )
    assert response.status_code == 200
    call.assert_called_once_with(JOB_ID, after_event=10, limit=500)

    invalid = api_client.get(
        f"/api/cameras/model-tests/{JOB_ID}/?after_event=-1&limit=501"
    )
    assert invalid.status_code == 400


def test_incompatible_success_contract_is_rejected(api_client, superuser):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "model_test_info", return_value=(200, {"enabled": True})):
        info = api_client.get("/api/cameras/model-tests/")
    with patch.object(
        ai,
        "model_test_status",
        return_value=(200, {"job_id": JOB_ID, "status": "running"}),
    ):
        detail = api_client.get(f"/api/cameras/model-tests/{JOB_ID}/")
    assert info.status_code == 502
    assert detail.status_code == 502
    assert info.data["code"] == detail.data["code"] == "model_test_contract_error"


def test_malformed_accepted_job_is_rejected(api_client, superuser):
    api_client.force_authenticate(superuser)
    with patch.object(
        ai,
        "upload_model_test",
        return_value=(
            202,
            {"job_id": "not-a-uuid", "status": "queued", "bundle": "production"},
        ),
    ):
        response = _video_post(api_client)
    assert response.status_code == 502
    assert response.data["code"] == "model_test_contract_error"


def test_upstream_401_is_not_exposed_as_browser_auth_failure(api_client, superuser):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "model_test_info", return_value=(401, {"error": "bad key"})):
        response = api_client.get("/api/cameras/model-tests/")
    assert response.status_code == 502
    assert response.data["code"] == "model_test_upstream_auth"
    assert "bad key" not in response.data["detail"]


class TrackingStream(BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class FakeResponse(BytesIO):
    status = 202


class FakeConnection:
    def __init__(self):
        self.request = None
        self.headers = []
        self.sent = []
        self.closed = False
        self.response = FakeResponse(
            json.dumps({"job_id": JOB_ID, "status": "queued"}).encode()
        )

    def putrequest(self, method, path, **kwargs):
        self.request = (method, path, kwargs)

    def putheader(self, name, value):
        self.headers.append((name, value))

    def endheaders(self):
        return None

    def send(self, chunk):
        self.sent.append(bytes(chunk))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_ai_upload_uses_bounded_chunks_and_exact_content_length(monkeypatch, settings):
    body = b"x" * (ai.MODEL_TEST_UPLOAD_CHUNK_BYTES * 2 + 17)
    stream = TrackingStream(body)
    connection = FakeConnection()
    monkeypatch.setattr(ai, "AI_URL", "http://camera.test:8890/base")
    monkeypatch.setattr(
        ai.http.client, "HTTPConnection", lambda *args, **kwargs: connection
    )
    settings.AI_MODEL_TEST_UPLOAD_TIMEOUT = 600

    status_code, payload = ai.upload_model_test(
        stream,
        content_length=len(body),
        content_type="video/mp4",
        query={"bundle": "production", "inference_fps": 12.0},
    )

    assert status_code == 202
    assert payload["job_id"] == JOB_ID
    assert connection.request[0] == "POST"
    assert (
        connection.request[1]
        == "/base/model-tests?bundle=production&inference_fps=12.0"
    )
    assert ("X-Api-Key", "backend-only-key") in connection.headers
    assert ("Content-Length", str(len(body))) in connection.headers
    assert stream.read_sizes == [
        ai.MODEL_TEST_UPLOAD_CHUNK_BYTES,
        ai.MODEL_TEST_UPLOAD_CHUNK_BYTES,
        17,
    ]
    assert b"".join(connection.sent) == body
    assert connection.closed
    assert connection.response.closed


def test_ai_upload_rejects_truncated_stream_and_closes_connection(
    monkeypatch, settings
):
    connection = FakeConnection()
    monkeypatch.setattr(ai, "AI_URL", "http://camera.test:8890")
    monkeypatch.setattr(
        ai.http.client, "HTTPConnection", lambda *args, **kwargs: connection
    )
    settings.AI_MODEL_TEST_UPLOAD_TIMEOUT = 600

    with pytest.raises(ai.ModelTestUploadInvalid) as exc_info:
        ai.upload_model_test(
            BytesIO(b"short"),
            content_length=100,
            content_type="video/mp4",
            query={"bundle": "production"},
        )
    assert exc_info.value.status == 400
    assert connection.closed


def test_malformed_upstream_error_is_mapped_to_gateway_failure(api_client, superuser):
    api_client.force_authenticate(superuser)
    with patch.object(
        ai,
        "model_test_info",
        side_effect=ai.AiError(503, "AI-сервис: ошибка 503"),
    ):
        response = api_client.get("/api/cameras/model-tests/")
    assert response.status_code == 502
    assert response.data["code"] == "ai_unavailable"


def test_detail_path_uses_canonical_uuid(api_client, superuser):
    api_client.force_authenticate(superuser)
    value = UUID(JOB_ID)
    with patch.object(
        ai, "model_test_status", return_value=(404, {"error": "gone"})
    ) as call:
        response = api_client.get(f"/api/cameras/model-tests/{value}/")
    assert response.status_code == 404
    assert response.data["detail"] == "gone"
    call.assert_called_once_with(str(value), after_event=0, limit=100)
