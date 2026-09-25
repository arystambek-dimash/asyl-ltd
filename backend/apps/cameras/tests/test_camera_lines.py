"""Прокси редактора линий камеры: линии проверки мешков и кадр для разметки."""

import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.cameras import ai, services

pytestmark = pytest.mark.django_db

URL = "/api/cameras/cam3/counting-line"
FRAME_URL = f"{URL}/frame"
JPEG = b"\xff\xd8\xff\xe0frame-bytes"
COUNT_LINE = {"x1": 0.57511491, "y1": 0.38398836, "x2": 0.58949159, "y2": 0.79174794}
BEFORE = {"id": "before", "name": "До механизма", "line": {"x1": 0.714, "y1": 0.354, "x2": 0.744, "y2": 0.716}}
AFTER = {"id": "after", "name": "После механизма", "line": {"x1": 0.189, "y1": 0.327, "x2": 0.189, "y2": 0.851}}
SAVED = {
    "cam": "cam3",
    "configured": True,
    "coordinate_space": "normalized",
    "line": COUNT_LINE,
    "line_spec": "0.57511491,0.38398836,0.58949159,0.79174794",
    "direction": "any",
    "updated_at": "2026-09-23T10:00:00.000+00:00",
}


@pytest.fixture(autouse=True)
def ai_key(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    keys = (ai.ALWAYS_ON_CACHE_KEY, ai.DETECTIONS_CACHE_KEY, services.CACHE_KEY, services.LAST_GOOD_CACHE_KEY)
    cache.delete_many(keys)
    yield
    cache.delete_many(keys)


@pytest.fixture
def root_client(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    return api_client


def _put(client, body):
    return client.put(URL, body, format="json")


def _body(**changes):
    return {"line": COUNT_LINE, "direction": "any", **changes}


# --- GET: pass-through and feature detection ----------------------------------


def test_get_passes_verification_lines_through(root_client):
    upstream = {**SAVED, "verification_lines": [BEFORE, AFTER]}
    with patch.object(ai, "_request", return_value=(200, upstream)):
        response = root_client.get(URL)

    assert response.status_code == 200
    assert response.data == {**upstream, "verification_lines_supported": True}


def test_get_from_older_ai_defaults_to_no_lines_and_reports_missing_support(root_client):
    with patch.object(ai, "_request", return_value=(200, SAVED)) as request:
        response = root_client.get(URL)

    assert response.status_code == 200
    assert response.data == {**SAVED, "verification_lines": [], "verification_lines_supported": False}
    request.assert_called_once_with("GET", "/cameras/cam3/line")


@pytest.mark.parametrize("upstream_status", [400, 401, 404])
def test_get_passes_upstream_error_status_and_body(root_client, upstream_status):
    upstream = {"detail": "upstream detail", "marker": upstream_status}
    with patch.object(ai, "_request", return_value=(upstream_status, upstream)):
        response = root_client.get(URL)

    assert response.status_code == upstream_status
    assert response.data == upstream


def test_get_maps_unavailable_ai_to_502(root_client):
    with patch.object(ai, "_request", side_effect=ai.AiUnavailable("network")):
        response = root_client.get(URL)

    assert response.status_code == 502
    assert response.data == {"detail": "AI-сервис камер недоступен", "code": "ai_unavailable"}


@pytest.mark.parametrize("bad_camera", ["2", "cam0", "cam02", "cam2/line"])
def test_noncanonical_camera_id_is_rejected_without_calling_ai(root_client, bad_camera):
    with patch.object(ai, "_request") as request:
        response = root_client.get(f"/api/cameras/{bad_camera}/counting-line")

    assert response.status_code in (400, 404)
    request.assert_not_called()


def test_ai_key_is_header_only_and_never_returned(root_client):
    upstream = _Upstream(body=b'{"cam":"cam3","configured":false}', content_type="application/json")
    with patch("urllib.request.urlopen", return_value=upstream) as urlopen:
        response = root_client.get(URL)

    request = urlopen.call_args.args[0]
    assert request.get_header("X-api-key") == "test-key"
    assert "test-key" not in request.full_url
    assert "test-key" not in response.content.decode()
    assert urlopen.call_args.kwargs["timeout"] == ai.TIMEOUT


# --- GET ?applied=1: is the running processor on the saved lines? -------------

SAVED_WITH_CHECKS = {
    **SAVED,
    "verification_lines": [{**BEFORE, "line_spec": "0.714,0.354,0.744,0.716"}],
}


def _processor(**changes):
    """``GET /processors/cam3`` of a live processor running ``SAVED_WITH_CHECKS``."""
    return {
        "cam": "cam3",
        "running": True,
        "processor_alive": True,
        "line": SAVED["line_spec"],
        "direction": "any",
        "verification": {"enabled": True, "lines": SAVED_WITH_CHECKS["verification_lines"]},
        **changes,
    }


def _serve(processor_status, processor=None):
    def request(method, path, body=None, **_options):
        if path == "/cameras/cam3/line":
            return 200, SAVED_WITH_CHECKS
        if path == "/processors/cam3":
            return processor_status, processor or {"error": "processor is not running"}
        raise AssertionError(path)

    return request


@pytest.mark.parametrize(
    ("processor_status", "processor", "state"),
    [
        (200, _processor(), "applied"),
        # Same numbers, other spelling of the spec: still the same line.
        (200, _processor(line="0.575114910,0.38398836,0.58949159,0.79174794"), "applied"),
        (200, _processor(line="0,0.5,1,0.5"), "not_applied"),
        (200, _processor(direction="positive"), "not_applied"),
        (200, _processor(verification={"enabled": False, "lines": []}), "not_applied"),
        (200, _processor(verification={"lines": [{**SAVED_WITH_CHECKS["verification_lines"][0], "name": "Старое"}]}), "not_applied"),
        # Nothing runs: the camera PC reads the saved file when the model starts.
        (404, None, "not_running"),
        (200, _processor(processor_alive=False), "not_running"),
    ],
)
def test_status_refresh_checks_the_running_processor(root_client, processor_status, processor, state):
    with patch.object(ai, "_request", side_effect=_serve(processor_status, processor)):
        response = root_client.get(URL, {"applied": "1"})

    assert response.status_code == 200
    assert response.data["line_applied"] == state
    assert response.data["verification_lines"] == SAVED_WITH_CHECKS["verification_lines"]


def test_older_processor_without_verification_status_is_judged_by_the_main_line(root_client):
    processor = _processor()
    del processor["verification"]
    with patch.object(ai, "_request", side_effect=_serve(200, processor)):
        response = root_client.get(URL, {"applied": "1"})

    assert response.data["line_applied"] == "applied"


def test_background_poll_does_not_ask_the_processor(root_client):
    with patch.object(ai, "_request", side_effect=_serve(200, _processor())) as request:
        response = root_client.get(URL)

    assert "line_applied" not in response.data
    request.assert_called_once_with("GET", "/cameras/cam3/line")


# --- PUT: absent vs [] ---------------------------------------------------------


def test_put_forwards_line_and_refreshes_every_line_cache(root_client):
    body = {"line": COUNT_LINE, "direction": "down"}
    upstream = {"ok": True, "saved": True, "applied_to_processor": True, **SAVED}
    old_line = {**SAVED, "line": {"x1": 0, "y1": 0.5, "x2": 1, "y2": 0.5}}
    inventory = [{"src": "cam3", "line_config": old_line}, {"src": "cam2", "line_config": None}]
    cache.set(services.CACHE_KEY, inventory, services.CACHE_TTL)
    cache.set(services.LAST_GOOD_CACHE_KEY, inventory, services.LAST_GOOD_TTL)
    cache.set(ai.ALWAYS_ON_CACHE_KEY, {"processors": [{"cam": "cam3", "line": "old"}]}, 30)
    cache.set(ai.DETECTIONS_CACHE_KEY, {"processors": [{"cam": "cam3", "line": "old"}]}, 30)
    with patch.object(ai, "_request", return_value=(200, upstream)) as request:
        response = _put(root_client, body)

    assert response.status_code == 200
    assert response.data == {**upstream, "verification_lines": [], "verification_lines_supported": False}
    request.assert_called_once_with("PUT", "/cameras/cam3/line", body)
    assert cache.get(ai.ALWAYS_ON_CACHE_KEY) is None
    assert cache.get(ai.DETECTIONS_CACHE_KEY) is None
    for key in (services.CACHE_KEY, services.LAST_GOOD_CACHE_KEY):
        refreshed = cache.get(key)
        assert refreshed[0]["line_config"] == SAVED
        assert refreshed[1]["line_config"] is None


def test_put_without_field_keeps_ai_side_verification_lines_untouched(root_client):
    upstream = {"ok": True, "saved": True, "applied_to_processor": True, **SAVED, "verification_lines": [BEFORE]}
    with patch.object(ai, "_request", return_value=(200, upstream)) as request:
        response = _put(root_client, _body())

    assert response.status_code == 200
    forwarded = request.call_args.args[2]
    assert "verification_lines" not in forwarded
    assert response.data["verification_lines"] == [BEFORE]


def test_put_empty_list_disables_verification(root_client):
    upstream = {"ok": True, "saved": True, "applied_to_processor": True, **SAVED, "verification_lines": []}
    with patch.object(ai, "_request", return_value=(200, upstream)) as request:
        response = _put(root_client, _body(verification_lines=[]))

    assert response.status_code == 200
    request.assert_called_once_with("PUT", "/cameras/cam3/line", _body(verification_lines=[]))


def test_put_forwards_normalized_verification_lines_and_patches_inventory_cache(root_client):
    sent = [
        {**BEFORE, "name": "  До механизма  "},
        {"id": "after", "name": "После механизма", "line": [0.189, 0.327, 0.189, 0.851]},
    ]
    upstream = {"ok": True, "saved": True, "applied_to_processor": True, **SAVED, "verification_lines": [BEFORE, AFTER]}
    inventory = [{"src": "cam3", "line_config": None}]
    cache.set(services.CACHE_KEY, inventory, services.CACHE_TTL)
    with patch.object(ai, "_request", return_value=(200, upstream)) as request:
        response = _put(root_client, _body(verification_lines=sent))

    assert response.status_code == 200
    assert request.call_args.args[2]["verification_lines"] == [BEFORE, AFTER]
    assert response.data["verification_lines"] == [BEFORE, AFTER]
    assert response.data["verification_lines_supported"] is True
    assert cache.get(services.CACHE_KEY)[0]["line_config"]["verification_lines"] == [BEFORE, AFTER]


def test_put_to_older_ai_reports_that_verification_lines_were_not_stored(root_client):
    upstream = {"ok": True, "saved": True, "applied_to_processor": True, **SAVED}
    with patch.object(ai, "_request", return_value=(200, upstream)):
        response = _put(root_client, _body(verification_lines=[BEFORE]))

    assert response.status_code == 200
    assert response.data["verification_lines"] == []
    assert response.data["verification_lines_supported"] is False


# --- PUT: server-side validation mirrors the camera PC --------------------------


@pytest.mark.parametrize("coordinate", [-0.01, 1.01])
def test_count_line_coordinate_outside_normalized_range_is_rejected(root_client, coordinate):
    with patch.object(ai, "_request") as request:
        response = _put(root_client, _body(line=[coordinate, 0.2, 0.8, 0.9]))

    assert response.status_code == 400
    assert "от 0 до 1" in response.data["detail"]
    request.assert_not_called()


@pytest.mark.parametrize("coordinate", [float("inf"), float("nan"), True, "0.2"])
def test_count_line_non_finite_or_non_numeric_coordinate_is_rejected(coordinate):
    with pytest.raises(ai.AiError) as error:
        ai.validate_counting_line(_body(line=[coordinate, 0.2, 0.8, 0.9]))
    assert error.value.status == 400


def test_count_line_identical_points_are_rejected(root_client):
    with patch.object(ai, "_request") as request:
        response = _put(root_client, _body(line=[0.25, 0.75, 0.25, 0.75], direction="positive"))

    assert response.status_code == 400
    assert "не должны совпадать" in response.data["detail"]
    request.assert_not_called()


@pytest.mark.parametrize("direction", ["any", "up", "down", "positive", "negative"])
def test_count_line_accepts_all_documented_directions(direction):
    body = {"line": [0.1, 0.2, 0.8, 0.9], "direction": direction}
    assert ai.validate_counting_line(body) == body


def test_count_line_unknown_direction_is_rejected(root_client):
    with patch.object(ai, "_request") as request:
        response = _put(root_client, _body(line=[0.1, 0.2, 0.8, 0.9], direction="sideways"))

    assert response.status_code == 400
    assert "direction" in response.data["detail"]
    request.assert_not_called()


def _line(identifier="check-1", name="Проверка 1", line=None, **extra):
    return {"id": identifier, "name": name, "line": line or {"x1": 0.2, "y1": 0.1, "x2": 0.2, "y2": 0.9}, **extra}


def _vertical(x):
    return {"x1": x, "y1": 0.1, "x2": x, "y2": 0.9}


@pytest.mark.parametrize(
    ("verification_lines", "message"),
    [
        (None, "список"),
        ("before", "список"),
        ({"id": "before"}, "список"),
        ([_line(f"check-{index}", line=_vertical(index / 10)) for index in range(1, 10)], "не более чем из 8"),
        (["before"], "должна содержать id"),
        ([_line(extra_field=True)], "должна содержать id"),
        ([_line("")], "ID линии проверки"),
        ([_line("проверка")], "ID линии проверки"),
        ([_line("has space")], "ID линии проверки"),
        ([_line("x" * 41)], "ID линии проверки"),
        ([_line(7)], "ID линии проверки"),
        ([_line("count")], "уникальными"),
        ([_line("same", line=_vertical(0.2)), _line("same", line=_vertical(0.3))], "уникальными"),
        ([_line(name="")], "Название линии проверки"),
        ([_line(name="   ")], "Название линии проверки"),
        ([_line(name="Я" * 81)], "Название линии проверки"),
        ([_line(name=5)], "Название линии проверки"),
        ([_line(line={"x1": 0.2, "y1": 0.1, "x2": 1.2, "y2": 0.9})], "от 0 до 1"),
        ([_line(line={"x1": 0.2, "y1": 0.1, "x2": True, "y2": 0.9})], "от 0 до 1"),
        ([_line(line={"x1": 0.2, "y1": 0.1, "x2": "0.2", "y2": 0.9})], "от 0 до 1"),
        ([_line(line={"x1": 0.2, "y1": 0.1})], "x1, y1, x2, y2"),
        ([_line(line=[0.2, 0.1, 0.3])], "четыре координаты"),
        ([_line(line={"x1": 0.4, "y1": 0.4, "x2": 0.4, "y2": 0.4})], "не должны совпадать"),
        ([_line(line=COUNT_LINE)], "совпадает с линией подсчёта"),
        (
            [_line(line={"x1": COUNT_LINE["x2"], "y1": COUNT_LINE["y2"], "x2": COUNT_LINE["x1"], "y2": COUNT_LINE["y1"]})],
            "совпадает с линией подсчёта",
        ),
        ([_line("a", line=_vertical(0.2)), _line("b", line=_vertical(0.2))], "совпадает с линией подсчёта"),
        (
            [_line("a", line=_vertical(0.2)), _line("b", line={"x1": 0.2, "y1": 0.9, "x2": 0.2, "y2": 0.1})],
            "совпадает с линией подсчёта",
        ),
    ],
)
def test_invalid_verification_lines_are_rejected_in_russian_before_the_ai_call(
    root_client, verification_lines, message
):
    with patch.object(ai, "_request") as request:
        response = _put(root_client, _body(verification_lines=verification_lines))

    assert response.status_code == 400
    assert message in response.data["detail"]
    request.assert_not_called()


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf")])
def test_non_finite_verification_coordinates_are_rejected(coordinate):
    with pytest.raises(ai.AiError) as error:
        ai.validate_counting_line(_body(verification_lines=[_line(line=[0.2, 0.1, coordinate, 0.9])]))
    assert error.value.status == 400


def test_eight_lines_legacy_line_spec_and_default_name_are_accepted():
    lines = [_line(f"check-{index}", line=_vertical(index / 10)) for index in range(1, 8)]
    lines.append({"id": "spec_8", "line_spec": "0.1,0.2,0.9,0.2"})

    validated = ai.validate_counting_line(_body(verification_lines=lines))

    assert len(validated["verification_lines"]) == 8
    assert validated["verification_lines"][-1] == {
        "id": "spec_8",
        "name": "spec_8",
        "line": {"x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 0.2},
    }


# --- PUT: saved but not applied --------------------------------------------------


def test_saved_but_not_applied_is_a_readable_partial_success(root_client):
    upstream = {
        "error": "camera processor is not running",
        "saved": True,
        "applied_to_processor": False,
        **SAVED,
        "verification_lines": [BEFORE],
    }
    inventory = [{"src": "cam3", "line_config": None}]
    cache.set(services.CACHE_KEY, inventory, services.CACHE_TTL)
    cache.set(services.LAST_GOOD_CACHE_KEY, inventory, services.LAST_GOOD_TTL)
    cache.set(ai.ALWAYS_ON_CACHE_KEY, {"processors": [{"cam": "cam3", "line": "old"}]}, 30)
    cache.set(ai.DETECTIONS_CACHE_KEY, {"processors": [{"cam": "cam3", "line": "old"}]}, 30)
    with patch.object(ai, "_request", return_value=(503, upstream)) as request:
        response = _put(root_client, _body(verification_lines=[BEFORE]))

    request.assert_called_once()
    assert response.status_code == 503
    # Returned once, without losing any field the camera PC sent.
    assert response.data == {
        **upstream,
        "code": "saved_not_applied",
        "detail": "Сохранено, но не применено к камере — обновите статус",
        "verification_lines_supported": True,
    }
    assert cache.get(ai.ALWAYS_ON_CACHE_KEY) is None
    assert cache.get(ai.DETECTIONS_CACHE_KEY) is None
    for key in (services.CACHE_KEY, services.LAST_GOOD_CACHE_KEY):
        assert cache.get(key)[0]["line_config"]["verification_lines"] == [BEFORE]


def test_rejected_ai_save_is_not_marked_as_saved(root_client):
    upstream = {"error": "verification lines must not repeat the count line", "cam": "cam3"}
    with patch.object(ai, "_request", return_value=(400, upstream)):
        response = _put(root_client, _body(verification_lines=[BEFORE]))

    assert response.status_code == 400
    assert response.data == upstream


# --- GET frame -----------------------------------------------------------------


class _Upstream:
    def __init__(self, body=JPEG, content_type="image/jpeg"):
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.status = 200
        self.closed = False

    def read(self, _size=-1):
        return self.body

    def close(self):
        self.closed = True


def _http_error(code, payload):
    return urllib.error.HTTPError(
        "http://ai/cameras/cam3/frame", code, "error", {}, BytesIO(json.dumps(payload).encode())
    )


def test_frame_proxy_returns_uncached_jpeg_and_keeps_the_ai_key_server_side(root_client):
    upstream = _Upstream()
    with patch("urllib.request.urlopen", return_value=upstream) as urlopen:
        response = root_client.get(FRAME_URL)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "no-store"
    assert response.content == JPEG
    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("/cameras/cam3/frame")
    assert request.get_header("X-api-key") == "test-key"
    assert b"test-key" not in response.content
    assert upstream.closed


@pytest.mark.parametrize(
    ("code", "status", "message"),
    [
        (503, 503, "Нет свежего кадра"),
        (404, 404, "не отдаёт кадр"),
        (401, 502, "ошибка 401"),
        (500, 502, "ошибка 500"),
    ],
)
def test_frame_proxy_maps_ai_errors_to_readable_messages(root_client, code, status, message):
    error = _http_error(code, {"error": "camera is not connected to AI; upload a reference frame"})
    with patch("urllib.request.urlopen", side_effect=error):
        response = root_client.get(FRAME_URL)

    assert response.status_code == status
    assert message in response.data["detail"]


@pytest.mark.parametrize(
    "upstream",
    [_Upstream(content_type="application/json"), _Upstream(body=b"not-a-jpeg"), _Upstream(body=JPEG + bytes(9 * 1024 * 1024))],
)
def test_frame_proxy_rejects_a_malformed_upstream_image(root_client, upstream):
    with patch("urllib.request.urlopen", return_value=upstream):
        response = root_client.get(FRAME_URL)

    assert response.status_code == 502
    assert response.data["code"] == "ai_unavailable"


def test_frame_proxy_maps_network_failure_to_502(root_client):
    with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
        response = root_client.get(FRAME_URL)

    assert response.status_code == 502
    assert response.data["detail"] == "AI-сервис камер недоступен"


def test_frame_proxy_rejects_a_noncanonical_camera_without_calling_ai(root_client):
    with patch("urllib.request.urlopen") as urlopen:
        response = root_client.get("/api/cameras/cam03/counting-line/frame")

    assert response.status_code == 400
    urlopen.assert_not_called()


def test_frame_proxy_reports_missing_ai_configuration(root_client, monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "")
    with patch("urllib.request.urlopen") as urlopen:
        response = root_client.get(FRAME_URL)

    assert response.status_code == 503
    assert response.data["code"] == "ai_disabled"
    urlopen.assert_not_called()


def test_line_editor_and_frame_proxy_are_superuser_only(api_client, operator, boss, client_user, admin_user):
    assert _put(api_client, _body()).status_code == 401
    assert api_client.get(FRAME_URL).status_code == 401
    for user in (operator, boss, client_user):
        api_client.force_authenticate(user)
        assert api_client.get(URL).status_code == 403
        assert _put(api_client, _body()).status_code == 403
        assert api_client.get(FRAME_URL).status_code == 403
    api_client.force_authenticate(admin_user)
    with patch.object(ai, "_request", return_value=(200, SAVED)):
        assert api_client.get(URL).status_code == 200
    with patch("urllib.request.urlopen", return_value=_Upstream()):
        assert api_client.get(FRAME_URL).status_code == 200
