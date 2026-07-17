"""Прокси AI-подсчёта мешков: маппинг ответов ai_service и права доступа."""
from unittest.mock import patch
from datetime import timedelta
from io import BytesIO

import pytest
from django.core import signing
from django.utils import timezone

from apps.cameras import ai, recordings
from apps.cameras.models import AiCountingSession
from apps.cameras.views import RECORDING_TOKEN_SALT
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

RUNNING = {
    "cam": "cam2", "running": True, "stream": "cam2ai", "status": "онлайн",
    "fps": 19.8, "total": 42, "weight": 2100, "per_color": {"Blue_50": 40},
}


@pytest.fixture(autouse=True)
def ai_key(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("loader", codes=["shipping.load"])


@pytest.fixture
def loading_order():
    client = Client.objects.create(first_name="AI", last_name="One", phone="1")
    return Order.objects.create(
        client=client,
        status="arrived",
        truck_number="01AI1",
        loading_camera="cam2",
    )


@pytest.fixture
def second_loading_order():
    client = Client.objects.create(first_name="AI", last_name="Two", phone="2")
    return Order.objects.create(
        client=client,
        status="arrived",
        truck_number="01AI2",
        loading_camera="cam2",
    )


def test_start_requires_camera_bound_by_monoblock(api_client, loader, loading_order):
    loading_order.loading_camera = ""
    loading_order.save(update_fields=["loading_camera"])
    api_client.force_authenticate(loader)

    with patch.object(ai, "_request") as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": loading_order.pk},
            format="json",
        )

    assert response.status_code == 400
    assert "Моноблок" in response.data["detail"]
    request.assert_not_called()


# --- клиент ---------------------------------------------------------------

def test_status_none_when_not_running():
    with patch.object(ai, "_request", return_value=(404, {"detail": "not running"})):
        assert ai.status("cam2") is None


def test_stop_returns_final_before_delete():
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path))
        return (200, RUNNING) if method == "GET" else (200, {})

    with patch.object(ai, "_request", side_effect=fake):
        final = ai.stop("cam2")
    assert final["total"] == 42
    assert calls == [("GET", "/processors/cam2"), ("DELETE", "/processors/cam2")]


def test_stop_when_not_running_skips_delete():
    with patch.object(ai, "_request", return_value=(404, {})) as req:
        assert ai.stop("cam2") is None
    assert req.call_count == 1  # только GET, DELETE не дёргаем


def test_normalize_accepts_known_shapes():
    assert ai.normalize("2") == "cam2"          # номер канала NVR
    assert ai.normalize("cam2") == "cam2"
    assert ai.normalize("cam_8c26") == "cam_8c26"  # direct по хвосту MAC


@pytest.mark.parametrize("bad", ["token", "cam/../x", "cam" + "x" * 20, ""])
def test_bad_camera_name_rejected_locally(bad):
    with pytest.raises(ai.AiError):  # до сервиса не ходим
        ai.status(bad)


# --- вьюхи ----------------------------------------------------------------

def test_get_status_without_session_is_fast_and_idle(api_client, operator, loading_order):
    api_client.force_authenticate(operator)
    with patch.object(ai, "_request") as request:
        resp = api_client.get(f"/api/cameras/cam2/ai/?order_id={loading_order.pk}")
    assert resp.status_code == 200
    assert resp.data["running"] is False
    assert resp.data["available"] is True
    request.assert_not_called()  # idle/busy polls do not wait for the camera PC


def test_start_attaches_to_same_order_without_reset(api_client, loader, loading_order):
    api_client.force_authenticate(loader)
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )

    def fake(method, path, body=None):
        assert method == "GET"  # повторный POST к сервису не уходит
        return 200, RUNNING

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.post("/api/cameras/cam2/ai/", {"order_id": loading_order.pk})
    assert resp.status_code == 200
    assert resp.data["total"] == 42
    assert resp.data["owned_by_order"] is True


def test_start_when_idle_posts_directly_to_service(api_client, loader, loading_order):
    api_client.force_authenticate(loader)
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path))
        assert (method, path) == ("POST", "/processors/cam2")
        return 200, {**RUNNING, "total": 0, "status": "запуск..."}

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.post("/api/cameras/cam2/ai/", {"order_id": loading_order.pk})
    assert resp.status_code == 200
    assert resp.data["total"] == 0
    assert calls == [("POST", "/processors/cam2")]
    session = AiCountingSession.objects.get()
    assert session.order == loading_order
    assert session.status == AiCountingSession.ACTIVE
    assert session.recording_stream == "cam2ai"


def test_start_accepts_order_id_from_query(api_client, loader, loading_order):
    """Shipping UI duplicates the selected order in query and JSON.

    Query support prevents body/proxy quirks from losing the order binding.
    """
    api_client.force_authenticate(loader)
    with patch.object(ai, "_request", return_value=(200, RUNNING)):
        resp = api_client.post(
            f"/api/cameras/cam2/ai/?order_id={loading_order.pk}",
            {},
            format="json",
        )
    assert resp.status_code == 200
    assert resp.data["session_order_id"] == loading_order.pk
    assert AiCountingSession.objects.get().order_id == loading_order.pk


def test_delete_returns_final_and_releases_slot(api_client, loader, loading_order):
    api_client.force_authenticate(loader)
    session = AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )

    def fake(method, path, body=None):
        return (200, RUNNING) if method == "GET" else (200, {})

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.delete(
            "/api/cameras/cam2/ai/", {"order_id": loading_order.pk}, format="json"
        )
    assert resp.status_code == 200
    assert resp.data["total"] == 42
    assert resp.data["running"] is False
    session.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED
    assert session.final_total == 42


def test_only_starter_or_admin_can_stop_session(
    api_client, loader, user_with_perms, loading_order,
):
    other_loader = user_with_perms("other-loader", codes=["shipping.load"])
    session = AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(other_loader)
    with patch.object(ai, "_request") as request:
        resp = api_client.delete(
            "/api/cameras/cam2/ai/", {"order_id": loading_order.pk}, format="json"
        )
    assert resp.status_code == 403
    assert "начавший" in resp.data["detail"]
    request.assert_not_called()
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE

    admin = user_with_perms("session-admin", codes=["shipping.load", "rbac.manage"])
    api_client.force_authenticate(admin)
    with patch.object(ai, "_request", side_effect=[(200, RUNNING), (200, {})]):
        resp = api_client.delete(
            "/api/cameras/cam2/ai/", {"order_id": loading_order.pk}, format="json"
        )
    assert resp.status_code == 200
    session.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED


def test_open_sessions_list_contains_owner_and_control_flag(
    api_client, loader, user_with_perms, loading_order,
):
    viewer = user_with_perms("session-viewer", codes=["shipping.load"])
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader, last_status={"total": 17},
    )

    api_client.force_authenticate(viewer)
    resp = api_client.get("/api/cameras/ai/sessions/")
    assert resp.status_code == 200
    assert resp.data[0]["camera"] == "cam2"
    assert resp.data[0]["started_by_name"] == "A B"
    assert resp.data[0]["can_stop"] is False
    assert resp.data[0]["last_status"]["total"] == 17

    api_client.force_authenticate(loader)
    resp = api_client.get("/api/cameras/ai/sessions/")
    assert resp.data[0]["can_stop"] is True


def test_open_sessions_require_load_permission(api_client, make_user):
    api_client.force_authenticate(make_user("session-plain-staff"))

    response = api_client.get("/api/cameras/ai/sessions/")

    assert response.status_code == 403


def test_open_sessions_include_every_department(api_client, loader):
    client = Client.objects.create(
        first_name="Field", last_name="Client", phone="3")
    order = Order.objects.create(
        client=client, department="field", status="arrived")
    AiCountingSession.objects.create(
        order=order, camera="cam3", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)

    response = api_client.get("/api/cameras/ai/sessions/")

    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["order_id"] == order.id


def test_limit_409_passes_through_and_releases_slot(api_client, loader, loading_order):
    api_client.force_authenticate(loader)

    def fake(method, path, body=None):
        return 409, {"detail": "лимит камер"}

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.post("/api/cameras/cam2/ai/", {"order_id": loading_order.pk})
    assert resp.status_code == 409
    assert "лимит" in resp.data["detail"]
    assert not AiCountingSession.objects.filter(
        status__in=AiCountingSession.OPEN_STATUSES
    ).exists()


def test_other_order_sees_busy_without_calling_worker(
    api_client, loader, loading_order, second_loading_order,
):
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)
    with patch.object(ai, "_request") as request:
        resp = api_client.get(
            f"/api/cameras/cam2/ai/?order_id={second_loading_order.pk}"
        )
    assert resp.status_code == 200
    assert resp.data["busy"] is True
    assert resp.data["session_order_id"] == loading_order.pk
    assert resp.data["running"] is False
    request.assert_not_called()


def test_other_order_cannot_start_until_owner_finishes(
    api_client, loader, loading_order, second_loading_order,
):
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)
    with patch.object(ai, "_request") as request:
        resp = api_client.post(
            "/api/cameras/cam2/ai/", {"order_id": second_loading_order.pk}
        )
    assert resp.status_code == 409
    assert resp.data["code"] == "ai_busy"
    assert resp.data["session_order_id"] == loading_order.pk
    request.assert_not_called()


def test_parallel_sessions_on_different_cameras(
    api_client, loader, loading_order, second_loading_order,
):
    """Две погрузки идут одновременно на разных камерах — обе стартуют."""
    from apps.cameras import sessions
    s1, created1 = sessions.reserve(loading_order, "cam2", loader)
    s2, created2 = sessions.reserve(second_loading_order, "cam3", loader)
    assert created1 and created2
    assert s1.pk != s2.pk
    open_ = set(
        AiCountingSession.objects
        .filter(status__in=AiCountingSession.OPEN_STATUSES)
        .values_list("camera", flat=True))
    assert open_ == {"cam2", "cam3"}
    # Второй заказ, встающий на УЖЕ занятую cam2 — конфликт.
    with pytest.raises(sessions.AiSessionBusy):
        sessions.reserve(second_loading_order, "cam2", loader)


def test_same_order_cannot_open_sessions_on_two_cameras(loader, loading_order):
    from apps.cameras import sessions
    first, created = sessions.reserve(loading_order, "cam2", loader)
    assert created is True
    with pytest.raises(sessions.AiSessionBusy) as exc:
        sessions.reserve(loading_order, "cam3", loader)
    assert exc.value.session.pk == first.pk
    assert AiCountingSession.objects.filter(
        order=loading_order, status__in=AiCountingSession.OPEN_STATUSES
    ).count() == 1


def test_current_for_camera_isolates_cameras(loader, loading_order, second_loading_order):
    from apps.cameras import sessions
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE, started_by=loader)
    AiCountingSession.objects.create(
        order=second_loading_order, camera="cam3", status=AiCountingSession.ACTIVE, started_by=loader)
    assert sessions.current_for_camera("cam2").order_id == loading_order.pk
    assert sessions.current_for_camera("cam3").order_id == second_loading_order.pk
    assert sessions.current_for_camera("cam9") is None


def test_missing_worker_marks_session_failed_and_unlocks(
    api_client, loader, loading_order,
):
    session = AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)
    with patch.object(ai, "_request", return_value=(404, {})):
        resp = api_client.get(f"/api/cameras/cam2/ai/?order_id={loading_order.pk}")
    assert resp.status_code == 200
    assert resp.data["code"] == "ai_processor_stopped"
    session.refresh_from_db()
    assert session.status == AiCountingSession.FAILED


def test_unavailable_maps_to_502(api_client, operator, loading_order):
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=operator,
    )
    api_client.force_authenticate(operator)
    with patch.object(ai, "_request", side_effect=ai.AiUnavailable("boom")):
        resp = api_client.get(f"/api/cameras/cam2/ai/?order_id={loading_order.pk}")
    assert resp.status_code == 502
    assert resp.data["code"] == "ai_unavailable"


def test_disabled_without_key(api_client, operator, monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "")
    api_client.force_authenticate(operator)
    resp = api_client.get("/api/cameras/cam2/ai/")
    assert resp.status_code == 503
    assert resp.data["code"] == "ai_disabled"


def test_mutations_require_shipping_load(api_client, make_user):
    staff = make_user("plain-staff")  # сотрудник без прав поста
    api_client.force_authenticate(staff)
    with patch.object(ai, "_request", return_value=(200, RUNNING)):
        assert api_client.get("/api/cameras/cam2/ai/").status_code == 200  # смотреть можно
        assert api_client.post("/api/cameras/cam2/ai/").status_code == 403
        assert api_client.delete("/api/cameras/cam2/ai/").status_code == 403
        assert api_client.post("/api/cameras/cam2/ai/reset/").status_code == 403


def test_clients_cannot_even_read(api_client, make_user):
    portal_client = make_user("portal", client=True)
    api_client.force_authenticate(portal_client)
    resp = api_client.get("/api/cameras/cam2/ai/")
    assert resp.status_code == 403


def test_history_returns_final_count_and_local_recording_metadata(
    api_client, user_with_perms, loader, loading_order,
):
    viewer = user_with_perms("history-viewer", codes=["shipping.view"])
    ended = timezone.now() - timedelta(hours=1)
    session = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        ended_at=ended,
        final_total=73,
        recording_stream="cam2ai",
        last_status={"total": 73, "stream": "cam2ai"},
    )
    api_client.force_authenticate(viewer)

    response = api_client.get(
        f"/api/cameras/ai/history/?order_id={loading_order.pk}"
    )

    assert response.status_code == 200
    assert response.data[0]["id"] == session.pk
    assert response.data[0]["final_total"] == 73
    assert response.data[0]["has_recording"] is True
    assert response.data[0]["recording_available_until"] is not None


def test_recording_list_is_resolved_on_camera_pc(
    api_client, user_with_perms, loader, loading_order,
):
    viewer = user_with_perms("recording-viewer", codes=["shipping.view"])
    session = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        ended_at=timezone.now(),
        final_total=12,
        recording_stream="cam2ai",
    )
    segment = {"start": "2026-07-17T10:00:00+06:00", "duration": 60.0}
    api_client.force_authenticate(viewer)

    with patch.object(recordings, "list_segments", return_value=[segment]) as listing:
        response = api_client.get(
            f"/api/cameras/ai/history/{session.pk}/recording/"
        )

    assert response.status_code == 200
    assert response.data["available"] is True
    assert response.data["retention_days"] == 14
    assert response.data["segments"][0]["video_url"].startswith("/api/cameras/ai/history/")
    assert listing.call_args.args[0] == "cam2ai"


def test_recording_video_proxies_bytes_without_server_storage(
    api_client, user_with_perms, loader, loading_order,
):
    viewer = user_with_perms("video-viewer", codes=["shipping.view"])
    session = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        ended_at=timezone.now(),
        recording_stream="cam2ai",
    )
    segment = {"start": "2026-07-17T10:00:00+06:00", "duration": 2.5}
    upstream = BytesIO(b"local-video")
    upstream.headers = {"Content-Length": "11"}
    api_client.force_authenticate(viewer)
    token = signing.dumps({
        "session": session.pk,
        "start": segment["start"],
        "duration": segment["duration"],
    }, salt=RECORDING_TOKEN_SALT)

    with patch.object(recordings, "list_segments", return_value=[segment]), \
         patch.object(recordings, "open_segment", return_value=upstream) as opening:
        response = api_client.get(
            f"/api/cameras/ai/history/{session.pk}/recording/video/",
            {"token": token},
        )
        content = b"".join(response.streaming_content)

    assert response.status_code == 200
    assert response["Content-Type"] == "video/mp4"
    assert content == b"local-video"
    opening.assert_called_once_with("cam2ai", segment["start"], 2.5)


def test_recording_archive_expires_but_count_metadata_remains(
    api_client, user_with_perms, loader, loading_order,
):
    viewer = user_with_perms("expired-video-viewer", codes=["shipping.view"])
    old = timezone.now() - timedelta(days=15)
    session = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        ended_at=old,
        final_total=81,
        recording_stream="cam2ai",
    )
    AiCountingSession.objects.filter(pk=session.pk).update(started_at=old)
    api_client.force_authenticate(viewer)

    history = api_client.get(f"/api/cameras/ai/history/?order_id={loading_order.pk}")
    with patch.object(recordings, "list_segments") as listing:
        archive = api_client.get(f"/api/cameras/ai/history/{session.pk}/recording/")

    assert history.status_code == 200
    assert history.data[0]["final_total"] == 81
    assert archive.status_code == 200
    assert archive.data["available"] is False
    listing.assert_not_called()


def test_local_playback_requests_browser_playable_fmp4():
    with patch.object(recordings, "_request", return_value=object()) as request:
        result = recordings.open_segment("cam2ai", "2026-07-17T10:00:00+06:00", 60)

    assert result is request.return_value
    assert "format=fmp4" in request.call_args.args[0]


def test_history_requires_shipping_view(api_client, make_user):
    api_client.force_authenticate(make_user("history-denied"))
    assert api_client.get("/api/cameras/ai/history/").status_code == 403
