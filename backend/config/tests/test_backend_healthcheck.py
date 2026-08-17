import io
import urllib.error

import healthcheck


def test_backend_healthcheck_uses_an_allowed_host(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "asyl-ltd.kz,www.asyl-ltd.kz")
    captured = {}

    def unavailable(request, *, timeout):
        captured["host"] = request.get_header("Host")
        assert timeout == 4
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", unavailable)

    assert healthcheck.main() == 1
    assert captured == {"host": "asyl-ltd.kz"}


def test_backend_healthcheck_keeps_non_server_http_responses_healthy(
    monkeypatch,
):
    response = io.BytesIO()

    def unauthorized(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            response,
        )

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", unauthorized)

    assert healthcheck.main() == 0
