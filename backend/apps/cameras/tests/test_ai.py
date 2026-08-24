"""Прокси AI-подсчёта мешков: маппинг ответов ai_service и права доступа."""
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core import signing
from django.core.cache import cache
from django.utils import timezone

from apps.cameras import ai, recordings, services
from apps.cameras.models import (
    AiCountingSession,
    MonoblockCameraSettings,
    MonoblockDevice,
)
from apps.cameras.views import RECORDING_TOKEN_SALT
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.grain import scale as grain_scale
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db

RUNNING = {
    "cam": "cam2", "running": True, "stream": "cam2ai", "status": "онлайн",
    "fps": 19.8, "total": 42, "weight": 2100, "per_color": {"Blue_50": 40},
}
LINE_CONFIG = {
    "cam": "cam2",
    "configured": True,
    "coordinate_space": "normalized",
    "line": {"x1": 0.08, "y1": 0.61, "x2": 0.93, "y2": 0.58},
    "line_spec": "0.08,0.61,0.93,0.58",
    "direction": "negative",
    "updated_at": "2026-07-20T18:00:00.000+00:00",
}


@pytest.fixture(autouse=True)
def ai_key(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    MonoblockCameraSettings.objects.create(camera_sources=["cam2", "cam3"])
    keys = (
        ai.ALWAYS_ON_CACHE_KEY,
        ai.DETECTIONS_CACHE_KEY,
        services.CACHE_KEY,
        services.LAST_GOOD_CACHE_KEY,
    )
    cache.delete_many(keys)
    yield
    cache.delete_many(keys)


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("loader", codes=["shipping.load"])


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="camera-root", password="pass12345"
    )


@pytest.fixture
def loading_order():
    client = Client.objects.create_with_user(first_name="AI", last_name="One", phone="1")
    return Order.objects.create(
        client=client,
        status="arrived",
        truck_number="01AI1",
        loading_camera="cam2",
    )


@pytest.fixture
def second_loading_order():
    client = Client.objects.create_with_user(first_name="AI", last_name="Two", phone="2")
    return Order.objects.create(
        client=client,
        status="arrived",
        truck_number="01AI2",
        loading_camera="cam3",
    )


def test_monoblock_start_binds_camera_and_moves_confirmed_order_to_loading(
    api_client, loader,
):
    client = Client.objects.create_with_user(first_name="AI", last_name="Waiting", phone="10")
    order = Order.objects.create(
        client=client,
        status="confirmed",
        truck_number="01WAIT",
    )
    api_client.force_authenticate(loader)

    with patch.object(ai, "_request", return_value=(200, RUNNING)) as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam2"
    assert order.shipment.loading_started_at is not None
    assert AiCountingSession.objects.get().order_id == order.pk
    assert [item.args for item in request.call_args_list] == [
        ("POST", "/processors/cam2", {}),
        ("POST", "/processors/cam2/reset", None),
    ]


def test_device_camera_conflict_is_rejected_before_worker_start(
    api_client,
    django_user_model,
):
    device_user = django_user_model.objects.create_user(
        username="cam2-device-conflict",
        password="pass12345",
    )
    MonoblockDevice.objects.create(
        user=device_user,
        name="Моноблок cam2",
        camera_source="cam2",
    )
    client = Client.objects.create_with_user(
        first_name="AI",
        last_name="Camera conflict",
        phone="camera-conflict",
    )
    order = Order.objects.create(
        client=client,
        status="confirmed",
        loading_camera="cam3",
    )
    api_client.force_authenticate(device_user)

    with patch.object(ai, "_request") as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 403
    request.assert_not_called()
    assert not AiCountingSession.objects.filter(order=order).exists()
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert order.loading_camera == "cam3"


def test_monoblock_start_ignores_scale_and_does_not_record_arrival(
    api_client, loader, settings,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    client = Client.objects.create_with_user(
        first_name="Order", last_name="Bound", phone="order-bound"
    )
    order = Order.objects.create(
        client=client,
        status="confirmed",
        truck_number="",
    )
    api_client.force_authenticate(loader)

    with patch.object(
        grain_scale,
        "read_truck_scale",
        side_effect=AssertionError("Monoblock must not read the physical scale"),
    ) as scale_read, patch.object(
        ai, "_request", return_value=(200, RUNNING)
    ) as ai_request:
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200
    order.refresh_from_db()
    shipment = Shipment.objects.get(order=order)
    session = AiCountingSession.objects.get(order=order)
    assert order.status == "loading"
    assert order.loading_camera == "cam2"
    assert shipment.truck_number == ""
    assert shipment.weigh_in_kg is None
    assert shipment.arrived_at is None
    assert shipment.shipped_at is None
    assert session.camera == "cam2"
    scale_read.assert_not_called()
    assert ai_request.called


def test_numberless_retry_on_another_camera_never_reads_scale(
    api_client, loader, settings,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    client = Client.objects.create_with_user(
        first_name="Retry", last_name="Bound", phone="retry-bound"
    )
    order = Order.objects.create(client=client, status="confirmed", truck_number="")
    api_client.force_authenticate(loader)

    with patch.object(
        grain_scale,
        "read_truck_scale",
        side_effect=AssertionError("AI retry must not read the physical scale"),
    ) as scale_read, patch.object(
        ai,
        "_request",
        side_effect=[
            (400, {"detail": "camera refused"}),
            (200, {**RUNNING, "cam": "cam3", "stream": "cam3ai"}),
            (200, {**RUNNING, "cam": "cam3", "stream": "cam3ai"}),
        ],
    ):
        refused = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )
        started = api_client.post(
            "/api/cameras/cam3/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert refused.status_code == 400
    assert started.status_code == 200
    order.refresh_from_db()
    shipment = Shipment.objects.get(order=order)
    active = AiCountingSession.objects.get(order=order, status=AiCountingSession.ACTIVE)
    scale_read.assert_not_called()
    assert order.loading_camera == "cam3"
    assert shipment.weigh_in_kg is None
    assert shipment.arrived_at is None
    assert active.camera == "cam3"


def test_unstable_scale_cannot_block_monoblock(
    api_client, loader, settings,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    client = Client.objects.create_with_user(
        first_name="Scale", last_name="Waiting", phone="scale-waiting"
    )
    order = Order.objects.create(
        client=client,
        status="confirmed",
        truck_number="01WAIT-SCALE",
    )
    api_client.force_authenticate(loader)

    with patch.object(
        grain_scale,
        "read_truck_scale",
        side_effect=grain_scale.TruckScaleNotReady(),
    ) as scale_read, patch.object(
        ai, "_request", return_value=(200, RUNNING)
    ) as ai_request:
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200
    scale_read.assert_not_called()
    assert ai_request.called
    order.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam2"
    assert order.shipment.weigh_in_kg is None
    assert order.shipment.arrived_at is None


def test_always_on_client_uses_windows_camera_sources_contract():
    upstream = {
        "camera_sources": ["cam2"], "source": "sub", "processors": [],
    }
    with patch.object(ai, "_call", return_value=upstream) as call:
        result = ai.configure_always_on(["2", "cam2"], "sub")

    call.assert_called_once_with(
        "PUT", "/always-on", {"camera_sources": ["cam2"], "source": "sub"},
    )
    assert result["cameras"] == ["cam2"]


def test_always_on_client_normalizes_status_and_falls_back_for_old_agent():
    with patch.object(ai, "_call", return_value={
        "camera_sources": ["cam3"], "source": "sub", "processors": [],
    }):
        assert ai.always_on_status()["cameras"] == ["cam3"]

    with patch.object(ai, "_call", side_effect=[
        ai.AiError(422, "Field required: cameras"),
        {"cameras": ["cam3"], "source": "sub", "processors": []},
    ]) as call:
        result = ai.configure_always_on(["cam3"])

    assert call.call_args_list[-1].args == (
        "PUT", "/always-on", {"cameras": ["cam3"], "source": "sub"},
    )
    assert result["cameras"] == ["cam3"]


def test_wagon_number_client_assigns_single_main_stream_camera():
    upstream = {
        "camera": "cam1",
        "source": "main",
        "stream": "cam1",
        "assigned": True,
        "mode": "wagon_number_24_7",
    }
    with patch.object(ai, "_call", return_value=upstream) as call:
        result = ai.configure_wagon_number("1")

    call.assert_called_once_with(
        "PUT",
        "/camera-roles/wagon-number",
        {"camera": "cam1", "source": "main"},
    )
    assert result == upstream


def test_monoblock_starts_confirmed_train_without_arrival_step(api_client, loader):
    client = Client.objects.create_with_user(first_name="AI", last_name="Train", phone="11")
    order = Order.objects.create(
        client=client,
        status="confirmed",
        transport_type="train",
    )
    api_client.force_authenticate(loader)

    with patch.object(ai, "_request", return_value=(200, {**RUNNING, "cam": "cam3"})):
        response = api_client.post(
            "/api/cameras/cam3/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam3"
    assert order.shipment.arrived_at is None
    assert order.shipment.weigh_in_kg is None
    assert order.shipment.loading_started_at is not None


def test_monoblock_rejects_unbound_order_that_is_already_loading(
    api_client, loader,
):
    client = Client.objects.create_with_user(first_name="AI", last_name="Already", phone="12")
    order = Order.objects.create(
        client=client,
        status="loading",
        truck_number="01LATE",
        loading_camera="",
    )
    api_client.force_authenticate(loader)

    with patch.object(ai, "_request") as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 400
    assert "подтверждённого или прибывшего" in response.data["detail"]
    assert not AiCountingSession.objects.filter(order=order).exists()
    request.assert_not_called()


# --- клиент ---------------------------------------------------------------

def test_status_none_when_not_running():
    with patch.object(ai, "_request", return_value=(404, {"detail": "not running"})):
        assert ai.status("cam2") is None


def test_delete_is_a_single_call_without_hidden_final_snapshot():
    with patch.object(ai, "_request", return_value=(200, {"running": False})) as req:
        assert ai.delete("cam2") == {"running": False}
    req.assert_called_once_with("DELETE", "/processors/cam2", None)


def test_normalize_accepts_known_shapes():
    assert ai.normalize("2") == "cam2"          # номер канала NVR
    assert ai.normalize("cam2") == "cam2"


@pytest.mark.parametrize("bad", ["token", "cam/../x", "cam_8c26", "cam02", "cam0", "cam" + "x" * 20, ""])
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


def test_starting_retry_adopts_live_count_without_reset(api_client, loader):
    client = Client.objects.create_with_user(
        first_name="AI", last_name="Retry", phone="retry"
    )
    order = Order.objects.create(client=client, status="confirmed")
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=loader,
    )
    api_client.force_authenticate(loader)
    counted = {**RUNNING, "total": 7, "mode": "session"}

    with patch.object(ai, "_request", return_value=(200, counted)) as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/", {"order_id": order.pk}, format="json"
        )

    assert response.status_code == 200
    assert response.data["total"] == 7
    request.assert_called_once_with("GET", "/processors/cam2", None)
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"


def test_stale_created_flag_cannot_reset_session_activated_by_parallel_start(
    api_client, loader, loading_order,
):
    """A delayed creator must derive its action from the locked row state."""
    session = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)

    with (
        patch(
            "apps.cameras.counting.sessions.reserve",
            return_value=(session, True),
        ),
        patch.object(ai, "_request", return_value=(200, RUNNING)) as request,
    ):
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": loading_order.pk},
            format="json",
        )

    assert response.status_code == 200
    request.assert_called_once_with("GET", "/processors/cam2", None)


def test_existing_session_restarts_worker_if_camera_pc_returned_idle(
    api_client, loader, loading_order,
):
    api_client.force_authenticate(loader)
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path))
        if method == "GET":
            return 200, {**RUNNING, "running": False, "warm": True, "total": 19}
        return 200, {**RUNNING, "total": 0}

    with patch.object(ai, "_request", side_effect=fake):
        response = api_client.post(
            "/api/cameras/cam2/ai/", {"order_id": loading_order.pk}, format="json"
        )

    assert response.status_code == 200
    assert response.data["running"] is True
    assert response.data["total"] == 0
    assert calls == [("GET", "/processors/cam2"), ("POST", "/processors/cam2")]


def test_open_session_reclaims_always_on_processor_after_windows_restart(
    api_client, loader, loading_order,
):
    api_client.force_authenticate(loader)
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, {
                **RUNNING, "mode": "always_on", "recording": False,
            }
        return 200, {
            **RUNNING, "mode": "session", "recording": True, "total": 0,
        }

    with patch.object(ai, "_request", side_effect=fake):
        response = api_client.get(
            f"/api/cameras/cam2/ai/?order_id={loading_order.pk}"
        )

    assert response.status_code == 200
    assert response.data["mode"] == "always_on"
    assert response.data["running"] is False
    assert response.data["code"] == "ai_reconciliation_required"
    # Поллинг остаётся read-only. Восстановление делает явный POST с правом
    # shipping.load, поэтому обычный сотрудник не может менять worker через GET.
    assert calls == [("GET", "/processors/cam2", None)]


def test_start_when_idle_posts_directly_to_service(api_client, loader, loading_order):
    api_client.force_authenticate(loader)
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        return 200, {**RUNNING, "total": 0, "status": "запуск..."}

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.post("/api/cameras/cam2/ai/", {"order_id": loading_order.pk})
    assert resp.status_code == 200
    assert resp.data["total"] == 0
    # Empty body makes ai_service use the camera's persisted line. In
    # particular, the removed legacy "0,0.5,1,0.5" is never sent.
    assert calls == [
        ("POST", "/processors/cam2", {}),
        ("POST", "/processors/cam2/reset", None),
    ]
    session = AiCountingSession.objects.get()
    assert session.order == loading_order
    assert session.status == AiCountingSession.ACTIVE
    assert session.recording_stream == "cam2ai"


# --- persisted counting line proxy ---------------------------------------

def test_get_saved_counting_line_preserves_upstream_body_and_status(
    api_client, superuser,
):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request", return_value=(200, LINE_CONFIG)) as request:
        response = api_client.get("/api/cameras/cam2/counting-line")

    assert response.status_code == 200
    assert response.data == LINE_CONFIG
    request.assert_called_once_with("GET", "/cameras/cam2/line")


def test_superuser_puts_valid_counting_line(api_client, superuser):
    body = {
        "line": {"x1": 0.08, "y1": 0.61, "x2": 0.93, "y2": 0.58},
        "direction": "down",
    }
    upstream = {
        "ok": True, "saved": True, "applied_to_processor": True,
        **LINE_CONFIG,
    }
    old_line = {**LINE_CONFIG, "line": {"x1": 0, "y1": 0.5, "x2": 1, "y2": 0.5}}
    inventory = [{"src": "cam2", "line_config": old_line}, {"src": "cam3", "line_config": None}]
    cache.set(services.CACHE_KEY, inventory, services.CACHE_TTL)
    cache.set(services.LAST_GOOD_CACHE_KEY, inventory, services.LAST_GOOD_TTL)
    cache.set(ai.ALWAYS_ON_CACHE_KEY, {"processors": [{"cam": "cam2", "line": "old"}]}, 30)
    cache.set(ai.DETECTIONS_CACHE_KEY, {"processors": [{"cam": "cam2", "line": "old"}]}, 30)
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request", return_value=(200, upstream)) as request:
        response = api_client.put(
            "/api/cameras/cam2/counting-line", body, format="json"
        )

    assert response.status_code == 200
    assert response.data == upstream
    request.assert_called_once_with("PUT", "/cameras/cam2/line", body)
    assert cache.get(ai.ALWAYS_ON_CACHE_KEY) is None
    assert cache.get(ai.DETECTIONS_CACHE_KEY) is None
    for key in (services.CACHE_KEY, services.LAST_GOOD_CACHE_KEY):
        refreshed = cache.get(key)
        assert refreshed[0]["line_config"] == LINE_CONFIG
        assert refreshed[1]["line_config"] is None


def test_fast_detection_snapshot_keeps_the_applied_counting_line():
    status = {
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "total": 7,
                "detections": [],
                "detection_frame": {"width": 1920, "height": 1080},
                "line": "0.08,0.61,0.93,0.58",
                "direction": "negative",
            }
        ]
    }
    with patch.object(ai, "always_on_status", return_value=status):
        payload = ai.always_on_detections_cached()

    assert payload["processors"][0]["line"] == "0.08,0.61,0.93,0.58"
    assert payload["processors"][0]["direction"] == "negative"


@pytest.mark.parametrize("coordinate", [-0.01, 1.01])
def test_counting_line_rejects_coordinate_outside_normalized_range(
    api_client, superuser, coordinate,
):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request") as request:
        response = api_client.put(
            "/api/cameras/cam2/counting-line",
            {"line": [coordinate, 0.2, 0.8, 0.9], "direction": "any"},
            format="json",
        )

    assert response.status_code == 400
    assert "от 0 до 1" in response.data["detail"]
    request.assert_not_called()


@pytest.mark.parametrize("coordinate", [float("inf"), float("nan"), True, "0.2"])
def test_counting_line_rejects_non_finite_or_non_numeric_coordinate(coordinate):
    with pytest.raises(ai.AiError) as exc:
        ai.validate_counting_line({
            "line": [coordinate, 0.2, 0.8, 0.9], "direction": "any",
        })
    assert exc.value.status == 400


def test_counting_line_rejects_identical_points(api_client, superuser):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request") as request:
        response = api_client.put(
            "/api/cameras/cam2/counting-line",
            {"line": [0.25, 0.75, 0.25, 0.75], "direction": "positive"},
            format="json",
        )

    assert response.status_code == 400
    assert "не должны совпадать" in response.data["detail"]
    request.assert_not_called()


@pytest.mark.parametrize("direction", ["any", "up", "down", "positive", "negative"])
def test_counting_line_accepts_all_documented_directions(direction):
    body = {"line": [0.1, 0.2, 0.8, 0.9], "direction": direction}
    assert ai.validate_counting_line(body) == body


def test_counting_line_rejects_unknown_direction(api_client, superuser):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request") as request:
        response = api_client.put(
            "/api/cameras/cam2/counting-line",
            {"line": [0.1, 0.2, 0.8, 0.9], "direction": "sideways"},
            format="json",
        )
    assert response.status_code == 400
    assert "direction" in response.data["detail"]
    request.assert_not_called()


@pytest.mark.parametrize("bad_camera", ["2", "cam0", "cam02", "cam2/line"])
def test_counting_line_rejects_noncanonical_camera_id(
    api_client, superuser, bad_camera,
):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request") as request:
        response = api_client.get(f"/api/cameras/{bad_camera}/counting-line")

    assert response.status_code in (400, 404)
    request.assert_not_called()


def test_saved_but_not_live_503_is_returned_once_without_field_loss(
    api_client, superuser,
):
    body = {"line": [0.08, 0.61, 0.93, 0.58], "direction": "any"}
    upstream = {
        "ok": False,
        "saved": True,
        "applied_to_processor": False,
        "cam": "cam2",
        "configured": True,
        "coordinate_space": "normalized",
        "line": LINE_CONFIG["line"],
        "line_spec": LINE_CONFIG["line_spec"],
        "direction": "any",
        "detail": "saved, live processor update pending",
    }
    inventory = [{"src": "cam2", "line_config": None}]
    cache.set(services.CACHE_KEY, inventory, services.CACHE_TTL)
    cache.set(services.LAST_GOOD_CACHE_KEY, inventory, services.LAST_GOOD_TTL)
    cache.set(ai.ALWAYS_ON_CACHE_KEY, {"processors": [{"cam": "cam2", "line": "old"}]}, 30)
    cache.set(ai.DETECTIONS_CACHE_KEY, {"processors": [{"cam": "cam2", "line": "old"}]}, 30)
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request", return_value=(503, upstream)) as request:
        response = api_client.put(
            "/api/cameras/cam2/counting-line", body, format="json"
        )

    assert response.status_code == 503
    assert response.data == upstream
    request.assert_called_once()
    assert cache.get(ai.ALWAYS_ON_CACHE_KEY) is None
    assert cache.get(ai.DETECTIONS_CACHE_KEY) is None
    assert cache.get(services.CACHE_KEY)[0]["line_config"]["line"] == LINE_CONFIG["line"]
    assert cache.get(services.LAST_GOOD_CACHE_KEY)[0]["line_config"]["line"] == LINE_CONFIG["line"]


@pytest.mark.parametrize("upstream_status", [400, 401, 404])
def test_counting_line_passes_upstream_error_status_and_body(
    api_client, superuser, upstream_status,
):
    upstream = {"detail": "upstream detail", "marker": upstream_status}
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request", return_value=(upstream_status, upstream)):
        response = api_client.get("/api/cameras/cam2/counting-line")
    assert response.status_code == upstream_status
    assert response.data == upstream


def test_counting_line_unavailable_maps_to_502(api_client, superuser):
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request", side_effect=ai.AiUnavailable("network")):
        response = api_client.get("/api/cameras/cam2/counting-line")
    assert response.status_code == 502
    assert response.data == {
        "detail": "AI-сервис камер недоступен", "code": "ai_unavailable",
    }


def test_ai_key_is_header_only_and_never_returned(api_client, superuser):
    class Upstream:
        status = 200
        closed = False

        def read(self, _size=-1):
            return b'{"cam":"cam2","configured":false}'

        def close(self):
            self.closed = True

    api_client.force_authenticate(superuser)
    with patch("urllib.request.urlopen", return_value=Upstream()) as urlopen:
        response = api_client.get("/api/cameras/cam2/counting-line")

    request = urlopen.call_args.args[0]
    assert request.get_header("X-api-key") == "test-key"
    assert "test-key" not in request.full_url
    assert "test-key" not in response.content.decode()
    assert urlopen.call_args.kwargs["timeout"] == ai.TIMEOUT


def test_only_superuser_can_access_counting_line(
    api_client, operator, boss, client_user, superuser,
):
    body = {"line": [0.1, 0.2, 0.8, 0.9], "direction": "up"}
    assert api_client.put(
        "/api/cameras/cam2/counting-line", body, format="json"
    ).status_code == 401
    api_client.force_authenticate(operator)
    assert api_client.put(
        "/api/cameras/cam2/counting-line", body, format="json"
    ).status_code == 403
    assert api_client.get("/api/cameras/cam2/counting-line").status_code == 403
    api_client.force_authenticate(boss)
    assert api_client.put(
        "/api/cameras/cam2/counting-line", body, format="json"
    ).status_code == 403
    assert api_client.get("/api/cameras/cam2/counting-line").status_code == 403
    api_client.force_authenticate(client_user)
    assert api_client.get("/api/cameras/cam2/counting-line").status_code == 403
    api_client.force_authenticate(superuser)
    with patch.object(ai, "_request", return_value=(200, LINE_CONFIG)):
        assert api_client.get("/api/cameras/cam2/counting-line").status_code == 200


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
    loading_order.refresh_from_db()
    assert loading_order.loading_camera == ""


def test_monoblock_stop_saves_ai_total_and_only_finishes_loading(
    api_client, loader, boss,
):
    product = Product.objects.create(
        name="AI final", color="Blue", weight_kg="50", price="100.00",
    )
    receive_stock(product, 100, boss)
    client = Client.objects.create_with_user(first_name="Final", last_name="Count", phone="20")
    order = Order.objects.create(
        client=client, status="confirmed", truck_number="01FINAL",
    )
    OrderItem.objects.create(order=order, product=product, quantity=50)
    api_client.force_authenticate(loader)

    with patch.object(ai, "_request", return_value=(200, RUNNING)):
        started = api_client.post(
            "/api/cameras/cam2/ai/", {"order_id": order.pk}, format="json",
        )
    assert started.status_code == 200

    with patch.object(
        ai, "_request", side_effect=[(200, RUNNING), (200, {"running": False})],
    ):
        completed = api_client.delete(
            "/api/cameras/cam2/ai/?complete_order=1",
            {"order_id": order.pk},
            format="json",
        )

    assert completed.status_code == 200
    assert completed.data["order_status"] == "loaded"
    assert completed.data["bags_loaded"] == 42

    # The loading-post permission may finish AI counting, but it must never
    # cross the separate shipping permission boundary or deduct stock.
    ship_attempt = api_client.post(f"/api/orders/{order.pk}/ship/", format="json")
    assert ship_attempt.status_code == 403

    order.refresh_from_db()
    assert order.status == "loaded"
    assert order.loading_camera == ""
    assert order.payment_status == "unpaid"
    assert order.is_debt is False
    assert order.shipment.bags_loaded == 42
    assert order.shipment.shipped_at is None
    assert StockItem.objects.get(product=product).bags == 100
    assert not EventLog.objects.filter(
        order=order,
        event_type__in=("debt", "shipment"),
    ).exists()
    session = AiCountingSession.objects.get(order=order)
    assert session.status == AiCountingSession.CLOSED
    assert session.final_total == 42


def test_monoblock_start_and_complete_never_read_physical_scale(
    api_client, loader, boss, settings,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    product = Product.objects.create(
        name="AI scale final", color="Blue", weight_kg="50", price="100.00",
    )
    receive_stock(product, 100, boss)
    client = Client.objects.create_with_user(
        first_name="Scale", last_name="AI", phone="scale-ai"
    )
    order = Order.objects.create(
        client=client,
        status="confirmed",
        truck_number="01AI-SCALE",
    )
    OrderItem.objects.create(order=order, product=product, quantity=50)
    api_client.force_authenticate(loader)

    with patch.object(
        grain_scale,
        "read_truck_scale",
        side_effect=AssertionError("Monoblock must not read Grain scales"),
    ) as read_scale:
        with patch.object(ai, "_request", return_value=(200, RUNNING)):
            started = api_client.post(
                "/api/cameras/cam2/ai/",
                {"order_id": order.pk},
                format="json",
            )
        with patch.object(
            ai,
            "_request",
            side_effect=[(200, RUNNING), (200, {"running": False})],
        ):
            completed = api_client.delete(
                "/api/cameras/cam2/ai/?complete_order=1",
                {"order_id": order.pk},
                format="json",
            )

    assert started.status_code == 200
    assert completed.status_code == 200
    read_scale.assert_not_called()
    order.refresh_from_db()
    assert order.status == "loaded"
    assert order.shipment.weigh_in_kg is None
    assert order.shipment.arrived_at is None
    assert order.shipment.shipped_at is None
    assert order.shipment.bags_loaded == 42
    assert StockItem.objects.get(product=product).bags == 100
    assert not EventLog.objects.filter(
        order=order,
        event_type__in=("arrival", "weigh_in", "weigh_out", "debt", "shipment"),
    ).exists()


def test_delete_commits_final_snapshot_before_worker_is_idled(
    api_client, loader, loading_order,
):
    api_client.force_authenticate(loader)
    session = AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    observed = []

    def fake(method, path, body=None):
        if method == "GET":
            return 200, RUNNING
        session.refresh_from_db()
        observed.append((session.final_total, session.last_status.get("total")))
        return 200, {"running": False}

    with patch.object(ai, "_request", side_effect=fake):
        response = api_client.delete(
            "/api/cameras/cam2/ai/", {"order_id": loading_order.pk}, format="json"
        )

    assert response.status_code == 200
    assert observed == [(42, 42)]


def test_failed_worker_cleanup_is_retried_before_camera_reuse(
    api_client, loader, loading_order,
):
    session = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)

    def unavailable_delete(method, path, body=None):
        if method == "GET":
            return 200, RUNNING
        raise ai.AiUnavailable("camera PC offline")

    with patch.object(ai, "_request", side_effect=unavailable_delete):
        stopped = api_client.delete(
            "/api/cameras/cam2/ai/",
            {"order_id": loading_order.pk},
            format="json",
        )

    assert stopped.status_code == 200
    assert stopped.data["cleanup_pending"] is True
    session.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED
    assert session.error.startswith("AI worker cleanup pending: ")

    next_client = Client.objects.create_with_user(
        first_name="AI", last_name="Next", phone="next"
    )
    next_order = Order.objects.create(client=next_client, status="confirmed")
    calls = []

    def recovered(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, RUNNING
        if method == "DELETE":
            return 200, {"running": False}
        return 200, {**RUNNING, "total": 0}

    with patch.object(ai, "_request", side_effect=recovered):
        started = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": next_order.pk},
            format="json",
        )

    assert started.status_code == 200
    assert calls == [
        ("GET", "/processors/cam2", None),
        ("DELETE", "/processors/cam2", None),
        ("POST", "/processors/cam2", {}),
        ("POST", "/processors/cam2/reset", None),
    ]
    session.refresh_from_db()
    assert session.error == ""


def test_starting_session_cannot_be_reported_as_completed_order(
    api_client, loader,
):
    client = Client.objects.create_with_user(
        first_name="AI", last_name="Pending", phone="pending"
    )
    order = Order.objects.create(client=client, status="confirmed")
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=loader,
    )
    api_client.force_authenticate(loader)

    with patch.object(ai, "_request") as request:
        response = api_client.delete(
            "/api/cameras/cam2/ai/?complete_order=1",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 409
    request.assert_not_called()
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.STARTING
    assert order.status == "confirmed"


def test_failed_business_completion_keeps_worker_and_rolls_back_snapshot(
    api_client, loader,
):
    client = Client.objects.create_with_user(
        first_name="AI", last_name="No shipment", phone="no-shipment"
    )
    order = Order.objects.create(
        client=client,
        status="loading",
        loading_camera="cam2",
    )
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        last_status={"total": 0},
    )
    api_client.force_authenticate(loader)

    with patch.object(ai, "_request", return_value=(200, RUNNING)) as request:
        response = api_client.delete(
            "/api/cameras/cam2/ai/?complete_order=1",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 400
    # GET captured the live total, but DELETE is after the business commit and
    # therefore never runs when shipment validation fails.
    request.assert_called_once_with("GET", "/processors/cam2", None)
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert session.final_total is None
    assert order.status == "loading"


def test_stop_never_uses_always_on_total_for_order(
    api_client, loader,
):
    product = Product.objects.create(
        name="AI fallback",
        color="Blue",
        weight_kg="50",
        price="100.00",
    )
    client = Client.objects.create_with_user(
        first_name="AI", last_name="Always", phone="always"
    )
    order = Order.objects.create(
        client=client,
        status="loading",
        loading_camera="cam2",
    )
    OrderItem.objects.create(order=order, product=product, quantity=13)
    Shipment.objects.create(order=order, loading_started_at=timezone.now())
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        # This is the normal checkpoint written by start. It is useful for the
        # UI but is not an authoritative final count after the worker changed
        # back to always-on mode.
        last_status={"total": 0, "stream": "cam2ai"},
    )
    api_client.force_authenticate(loader)
    always_on = {
        **RUNNING,
        "total": 999,
        "mode": "always_on",
        "recording": False,
    }

    with patch.object(ai, "_request", return_value=(200, always_on)) as request:
        response = api_client.delete(
            "/api/cameras/cam2/ai/?complete_order=1",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["bags_loaded"] == 13
    request.assert_called_once_with("GET", "/processors/cam2", None)
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.final_total == 13
    assert order.status == "loaded"
    assert order.shipment.bags_loaded == 13
    assert order.shipment.shipped_at is None


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

    admin = user_with_perms(
        "session-admin", codes=["shipping.load", "sys_permissions.manage"]
    )
    api_client.force_authenticate(admin)
    with patch.object(ai, "_request", side_effect=[(200, RUNNING), (200, {})]):
        resp = api_client.delete(
            "/api/cameras/cam2/ai/", {"order_id": loading_order.pk}, format="json"
        )
    assert resp.status_code == 200
    session.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED


def test_only_starter_or_admin_can_recover_session(
    api_client, loader, user_with_perms, loading_order,
):
    other_loader = user_with_perms("other-recovery", codes=["shipping.load"])
    AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(other_loader)

    with patch.object(ai, "_request") as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/",
            {"order_id": loading_order.pk},
            format="json",
        )

    assert response.status_code == 403
    assert "начавший" in response.data["detail"]
    request.assert_not_called()


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
    client = Client.objects.create_with_user(
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


def test_missing_worker_status_is_read_only_and_keeps_reservation(
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
    assert session.status == AiCountingSession.ACTIVE
    loading_order.refresh_from_db()
    assert loading_order.loading_camera == "cam2"


def test_idle_worker_status_is_read_only_and_keeps_reservation(
    api_client, loader, loading_order,
):
    session = AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)
    with patch.object(ai, "_request", return_value=(200, {
        **RUNNING, "running": False, "warm": True,
    })):
        response = api_client.get(
            f"/api/cameras/cam2/ai/?order_id={loading_order.pk}"
        )
    assert response.status_code == 200
    assert response.data["code"] == "ai_processor_stopped"
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    loading_order.refresh_from_db()
    assert loading_order.loading_camera == "cam2"


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


def test_history_post_board_projection_filters_server_side(
    api_client,
    user_with_perms,
):
    viewer = user_with_perms("board-history", codes=["shipping.view"])
    client = Client.objects.create_with_user(first_name="Board", phone="1")
    active = Order.objects.create(client=client, status="loading")
    outside = Order.objects.create(client=client, status="pending")
    active_session = AiCountingSession.objects.create(
        order=active,
        camera="cam1",
        status=AiCountingSession.CLOSED,
        final_total=7,
    )
    AiCountingSession.objects.create(
        order=outside,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        final_total=8,
    )
    api_client.force_authenticate(viewer)

    response = api_client.get("/api/cameras/ai/history/?post_board=1")

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [active_session.id]


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
