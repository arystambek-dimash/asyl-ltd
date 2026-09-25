"""Прокси AI-подсчёта мешков: маппинг ответов ai_service и права доступа."""
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.cameras import ai, counting, services, sessions
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AiCountingSession,
    MonoblockCameraSettings,
)
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db

RUNNING = {
    "cam": "cam2", "running": True, "stream": "cam2ai", "status": "онлайн",
    "fps": 19.8, "total": 42, "weight": 2100, "per_color": {"Blue_50": 40},
    "continuous_analytics": True,
    "analytics_scope": ANALYTICS_SCOPE_SHIPPING,
}


def order_session_status(
    session_id: int,
    *,
    cam: str = "cam2",
    total: int = 0,
    running: bool = True,
    **updates,
) -> dict:
    return {
        **RUNNING,
        "cam": cam,
        "stream": f"{cam}ai",
        "running": running,
        "mode": "session",
        "session_id": session_id,
        "total": total,
        **updates,
    }


def durable_start_request(method, path, body=None):
    assert method == "POST"
    assert isinstance(body, dict)
    assert body["expected_analytics_scope"] == ANALYTICS_SCOPE_SHIPPING
    camera = path.removeprefix("/processors/")
    return 200, order_session_status(body["session_id"], cam=camera)


def durable_stop_response(
    session_id: int,
    *,
    total: int = 42,
    final_updates: dict | None = None,
) -> dict:
    final = {
        **RUNNING,
        "running": False,
        "mode": "session",
        "session_id": session_id,
        "total": total,
        **(final_updates or {}),
    }
    return {
        "ok": True,
        "cam": "cam2",
        "stopped": True,
        "mode": "always_on",
        "session_id": session_id,
        "final": final,
        "processor": {"cam": "cam2", "mode": "always_on"},
        "always_on": {},
    }


def _order(phone: str, **fields) -> Order:
    client = Client.objects.create_with_user(first_name="AI", last_name=phone, phone=phone)
    return Order.objects.create(client=client, **fields)


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
    return user_with_perms("loader", codes=["monoblock.view", "loader.confirm", "loader.trucks", "loader.wagons"])


@pytest.fixture
def loading_order():
    return _order("1", status="arrived", truck_number="01AI1", loading_camera="cam2")


@pytest.fixture
def active_session(loader, loading_order):
    return AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
    )


@pytest.fixture
def second_loading_order():
    return _order("2", status="arrived", truck_number="01AI2", loading_camera="cam3")


def test_monoblock_start_binds_camera_and_moves_confirmed_order_to_loading(loader):
    order = _order("10", status="confirmed", truck_number="01WAIT")

    with patch.object(ai, "_request", side_effect=durable_start_request) as request:
        counting.start("cam2", order, loader)

    order.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam2"
    assert order.shipment.loading_started_at is not None
    # Погрузка в Моноблоке не записывает приезд и вес: это работа весовой.
    assert order.shipment.weigh_in_kg is None
    assert order.shipment.arrived_at is None
    assert order.shipment.shipped_at is None
    session = AiCountingSession.objects.get()
    assert session.order_id == order.pk
    assert [item.args for item in request.call_args_list] == [
        (
            "POST",
            "/processors/cam2",
            {
                "source": "sub",
                "session_id": session.pk,
                "require_continuous": True,
                "expected_analytics_scope": ANALYTICS_SCOPE_SHIPPING,
            },
        ),
    ]


def test_monoblock_accepts_first_crossing_during_durable_session_start(
    loader, monkeypatch,
):
    order = _order("async-start", status="confirmed")
    session_payloads = []
    monkeypatch.setattr(ai, "SESSION_READY_POLL_SECONDS", 0)

    with patch.object(
        ai,
        "_request",
        side_effect=lambda method, path, body=None: (
            session_payloads.append(body["session_id"])
            or (
                200,
                order_session_status(
                    body["session_id"],
                    total=19,
                    running=False,
                ),
            )
            if method == "POST"
            else (
                200,
                order_session_status(session_payloads[-1], total=19),
            )
        ),
    ) as request:
        result = counting.start("cam2", order, loader)

    assert result["running"] is True
    # The durable session boundary was zero at activation. A bag may cross
    # before the async readiness poll returns, and that first valid event must
    # never make startup time out or trigger cleanup.
    assert result["total"] == 19
    session = AiCountingSession.objects.get(order=order)
    assert [item.args for item in request.call_args_list] == [
        (
            "POST",
            "/processors/cam2",
            {
                "source": "sub",
                "session_id": session.pk,
                "require_continuous": True,
                "expected_analytics_scope": ANALYTICS_SCOPE_SHIPPING,
            },
        ),
        ("GET", "/processors/cam2", None),
    ]
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"


def test_numberless_retry_on_another_camera_rebinds_order(loader):
    order = _order("retry-bound", status="confirmed", truck_number="")

    def fake_ai(method, path, body=None):
        if path == "/processors/cam2":
            return 400, {"detail": "camera refused"}
        return durable_start_request(method, path, body)

    with patch.object(ai, "_request", side_effect=fake_ai):
        with pytest.raises(ai.AiError) as refused:
            counting.start("cam2", order, loader)
        counting.start("cam3", order, loader)

    assert refused.value.status == 400
    order.refresh_from_db()
    shipment = Shipment.objects.get(order=order)
    active = AiCountingSession.objects.get(order=order, status=AiCountingSession.ACTIVE)
    assert order.loading_camera == "cam3"
    assert shipment.weigh_in_kg is None
    assert shipment.arrived_at is None
    assert active.camera == "cam3"


def test_always_on_client_uses_role_aware_camera_sources_contract():
    upstream = {
        "camera_sources": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        "processors": [],
    }
    with patch.object(ai, "_call", return_value=upstream) as call:
        result = ai.configure_always_on(
            ["2", "cam2"],
            "sub",
            analytics_scopes={"2": ANALYTICS_SCOPE_AI247},
        )

    call.assert_called_once_with(
        "PUT",
        "/always-on",
        {
            "camera_sources": ["cam2"],
            "source": "sub",
            "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        },
    )
    assert result == upstream
    assert result["analytics_scopes"] == {"cam2": ANALYTICS_SCOPE_AI247}


def test_always_on_client_never_retries_an_old_agent():
    with (
        patch.object(
            ai,
            "_call",
            side_effect=ai.AiError(422, "Field required: cameras"),
        ) as call,
        pytest.raises(ai.AiError) as exc,
    ):
        ai.configure_always_on(
            ["cam3"],
            analytics_scopes={"cam3": ANALYTICS_SCOPE_AI247},
        )

    assert exc.value.status == 422
    call.assert_called_once_with(
        "PUT",
        "/always-on",
        {
            "camera_sources": ["cam3"],
            "source": "sub",
            "analytics_scopes": {"cam3": ANALYTICS_SCOPE_AI247},
        },
    )


@pytest.mark.parametrize(
    "analytics_scopes",
    [
        None,
        {},
        {"cam2": "legacy"},
        {
            "cam2": ANALYTICS_SCOPE_AI247,
            "cam3": ANALYTICS_SCOPE_SHIPPING,
        },
    ],
)
def test_always_on_client_requires_one_exact_role_per_camera(analytics_scopes):
    with (
        patch.object(ai, "_call") as call,
        pytest.raises(ai.AiError) as exc,
    ):
        ai.configure_always_on(
            ["cam2"],
            analytics_scopes=analytics_scopes,
        )

    assert exc.value.status == 400
    call.assert_not_called()


def test_monoblock_starts_confirmed_train_without_arrival_step(loader):
    order = _order("11", status="confirmed", transport_type="train")

    with patch.object(
        ai, "_request", side_effect=durable_start_request
    ):
        counting.start("cam3", order, loader)

    order.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam3"
    assert order.shipment.arrived_at is None
    assert order.shipment.weigh_in_kg is None
    assert order.shipment.loading_started_at is not None


def test_monoblock_rejects_unbound_order_that_is_already_loading(loader):
    order = _order("12", status="loading", truck_number="01LATE", loading_camera="")

    with (
        patch.object(ai, "_request") as request,
        pytest.raises(ai.AiError) as exc,
    ):
        counting.start("cam2", order, loader)

    assert exc.value.status == 400
    assert "подтверждённого или прибывшего" in exc.value.detail
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


# --- сервис подсчёта -------------------------------------------------------

def test_start_attaches_to_same_order_without_reset(loader, loading_order, active_session):

    def fake(method, path, body=None):
        assert method == "GET"  # повторный POST к сервису не уходит
        return 200, order_session_status(active_session.pk, total=42)

    with patch.object(ai, "_request", side_effect=fake):
        result = counting.start("cam2", loading_order, loader)
    assert result["total"] == 42
    assert result["owned_by_order"] is True


@pytest.mark.parametrize("service", ["start", "stop"])
def test_stale_session_id_rejects_ai_mutation(loader, loading_order, active_session, service):

    with (
        patch.object(ai, "_request") as request,
        pytest.raises(ai.AiError) as exc,
    ):
        getattr(counting, service)(
            "cam2",
            loading_order,
            loader,
            expected_session_id=active_session.pk + 1,
        )

    assert exc.value.status == 409
    assert "AI-сессия изменилась" in exc.value.detail
    request.assert_not_called()
    active_session.refresh_from_db()
    assert active_session.status == AiCountingSession.ACTIVE


def test_starting_retry_adopts_live_count_without_reset(loader):
    order = _order("retry", status="confirmed")
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=loader,
    )
    counted = order_session_status(session.pk, total=7)

    with patch.object(ai, "_request", return_value=(200, counted)) as request:
        result = counting.start("cam2", order, loader)

    assert result["total"] == 7
    request.assert_called_once_with("GET", "/processors/cam2", None)
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"


def test_stale_created_flag_cannot_reset_session_activated_by_parallel_start(
    loader, loading_order, active_session,
):
    """A delayed creator must derive its action from the locked row state."""

    with (
        patch(
            "apps.cameras.counting.sessions.reserve",
            return_value=(active_session, True),
        ),
        patch.object(
            ai,
            "_request",
            return_value=(200, order_session_status(active_session.pk, total=42)),
        ) as request,
    ):
        counting.start("cam2", loading_order, loader)

    request.assert_called_once_with("GET", "/processors/cam2", None)


def test_existing_session_restarts_worker_if_camera_pc_returned_idle(
    loader, loading_order, active_session,
):
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path))
        if method == "GET":
            return 200, {
                **RUNNING,
                "running": False,
                "mode": "idle",
                "warm": True,
                "total": 19,
            }
        return 200, order_session_status(active_session.pk)

    with patch.object(ai, "_request", side_effect=fake):
        result = counting.start("cam2", loading_order, loader)

    assert result["running"] is True
    assert result["total"] == 0
    assert calls == [("GET", "/processors/cam2"), ("POST", "/processors/cam2")]


def test_start_when_idle_posts_directly_to_service(loader, loading_order):
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        return 200, order_session_status(
            body["session_id"],
            status="starting",
        )

    with patch.object(ai, "_request", side_effect=fake):
        result = counting.start("cam2", loading_order, loader)
    assert result["total"] == 0
    session = AiCountingSession.objects.get()
    # The source is explicit while the line remains absent, so ai_service uses
    # the camera's persisted line and the removed legacy default is never sent.
    assert calls == [
        (
            "POST",
            "/processors/cam2",
            {
                "source": "sub",
                "session_id": session.pk,
                "require_continuous": True,
                "expected_analytics_scope": ANALYTICS_SCOPE_SHIPPING,
            },
        ),
    ]
    assert session.order == loading_order
    assert session.status == AiCountingSession.ACTIVE
    assert session.recording_stream == "cam2ai"


@pytest.mark.parametrize(
    ("reply_mutation", "detail_fragment"),
    [
        ({"continuous_analytics": False}, "контуре отгрузки"),
        ({"analytics_scope": ANALYTICS_SCOPE_AI247}, "контуре отгрузки"),
        ({"session_id_offset": 1}, "другой сессии"),
    ],
)
def test_start_rejects_wrong_contour_or_durable_worker_identity(
    loader,
    loading_order,
    reply_mutation,
    detail_fragment,
):
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if method == "DELETE":
            return 200, durable_stop_response(body["session_id"])
        worker_session_id = body["session_id"] + reply_mutation.get(
            "session_id_offset",
            0,
        )
        return 200, {
            **RUNNING,
            "mode": "session",
            "total": 0,
            "continuous_analytics": reply_mutation.get(
                "continuous_analytics",
                True,
            ),
            "analytics_scope": reply_mutation.get(
                "analytics_scope",
                ANALYTICS_SCOPE_SHIPPING,
            ),
            "session_id": worker_session_id,
        }

    with (
        patch.object(ai, "_request", side_effect=fake),
        pytest.raises(ai.AiError) as exc,
    ):
        counting.start("cam2", loading_order, loader)

    assert exc.value.status == 409
    assert detail_fragment in exc.value.detail
    session = AiCountingSession.objects.get(order=loading_order)
    assert session.status == AiCountingSession.FAILED
    assert calls == [
        (
            "POST",
            "/processors/cam2",
            {
                "source": "sub",
                "session_id": session.pk,
                "require_continuous": True,
                "expected_analytics_scope": ANALYTICS_SCOPE_SHIPPING,
            },
        ),
        ("DELETE", "/processors/cam2", {"session_id": session.pk}),
    ]
    loading_order.refresh_from_db()
    assert loading_order.status == "arrived"
    assert loading_order.loading_camera == ""


def test_fast_detection_snapshot_keeps_the_applied_counting_line():
    status = {
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "total": 7,
                "bags_present": True,
                "detections": [],
                "detection_frame": {"width": 1920, "height": 1080},
                "line": "0.08,0.61,0.93,0.58",
                "direction": "negative",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
            }
        ]
    }
    with patch.object(ai, "always_on_status", return_value=status):
        payload = ai.always_on_detections_cached()

    assert payload["processors"][0]["line"] == "0.08,0.61,0.93,0.58"
    assert payload["processors"][0]["direction"] == "negative"
    assert payload["processors"][0]["bags_present"] is True
    assert (
        payload["processors"][0]["analytics_scope"]
        == ANALYTICS_SCOPE_AI247
    )


@pytest.mark.parametrize(
    ("upstream", "expected"),
    [(False, False), (None, None), ("false", None), (0, None)],
)
def test_fast_detection_snapshot_preserves_bag_presence_tristate(
    upstream,
    expected,
):
    status = {
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "bags_present": upstream,
            }
        ]
    }
    with patch.object(ai, "always_on_status", return_value=status):
        payload = ai.always_on_detections_cached()

    assert payload["processors"][0]["bags_present"] is expected


def test_stop_returns_final_and_releases_slot(loader, loading_order, active_session):

    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, {
                **RUNNING,
                "mode": "session",
                "session_id": active_session.pk,
            }
        return 200, durable_stop_response(active_session.pk)

    with patch.object(ai, "_request", side_effect=fake):
        result = counting.stop("cam2", loading_order, loader)
    assert result["total"] == 42
    assert result["running"] is False
    active_session.refresh_from_db()
    assert active_session.status == AiCountingSession.CLOSED
    assert active_session.final_total == 42
    assert calls == [
        ("GET", "/processors/cam2", None),
        ("DELETE", "/processors/cam2", {"session_id": active_session.pk}),
    ]
    loading_order.refresh_from_db()
    assert loading_order.loading_camera == ""


def test_monoblock_stop_saves_ai_total_and_only_finishes_loading(loader, boss):
    product = Product.objects.create(
        name="AI final", color="Blue", weight_kg="50",
    )
    receive_stock(product, 100, boss)
    order = _order("20", status="confirmed", truck_number="01FINAL")
    OrderItem.objects.create(order=order, product=product, quantity=50)

    with patch.object(ai, "_request", side_effect=durable_start_request):
        counting.start("cam2", order, loader)
    session = AiCountingSession.objects.get(order=order)

    with patch.object(
        ai,
        "_request",
        return_value=(200, durable_stop_response(session.pk, total=43)),
    ) as request:
        completed = counting.stop("cam2", order, loader, complete_order=True)

    assert completed["order_status"] == "loaded"
    assert completed["bags_loaded"] == 43
    request.assert_called_once_with(
        "DELETE",
        "/processors/cam2",
        {"session_id": session.pk},
    )

    # Завершение подсчёта не отгружает: списание со склада — отдельное действие грузчика.
    order.refresh_from_db()
    assert order.status == "loaded"
    assert order.loading_camera == ""
    assert order.payment_status == "unpaid"
    assert order.is_debt is False
    assert order.shipment.bags_loaded == 43
    assert order.shipment.shipped_at is None
    assert order.shipment.weigh_in_kg is None
    assert order.shipment.arrived_at is None
    assert StockItem.objects.get(product=product).bags == 100
    assert not EventLog.objects.filter(
        order=order,
        event_type__in=("arrival", "weigh_in", "weigh_out", "debt", "shipment"),
    ).exists()
    session.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED
    assert session.final_total == 43
    assert session.last_status["total"] == 43


def test_stop_commits_final_snapshot_before_worker_is_idled(loader, loading_order, active_session):
    observed = []

    def fake(method, path, body=None):
        if method == "GET":
            return 200, order_session_status(active_session.pk, total=42)
        active_session.refresh_from_db()
        observed.append((active_session.final_total, active_session.last_status.get("total")))
        return 200, durable_stop_response(active_session.pk)

    with patch.object(ai, "_request", side_effect=fake):
        counting.stop("cam2", loading_order, loader)

    assert observed == [(42, 42)]


def test_failed_worker_cleanup_is_retried_before_camera_reuse(
    loader, loading_order, active_session,
):

    def unavailable_delete(method, path, body=None):
        if method == "GET":
            return 200, order_session_status(active_session.pk, total=42)
        raise ai.AiUnavailable("camera PC offline")

    with patch.object(ai, "_request", side_effect=unavailable_delete):
        stopped = counting.stop("cam2", loading_order, loader)

    assert stopped["cleanup_pending"] is True
    active_session.refresh_from_db()
    assert active_session.status == AiCountingSession.CLOSED
    assert active_session.error.startswith("AI worker cleanup pending: ")

    next_order = _order("next", status="confirmed")
    calls = []

    def recovered(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, {**RUNNING, "mode": "always_on"}
        if method == "DELETE":
            return 200, durable_stop_response(active_session.pk)
        return durable_start_request(method, path, body)

    with patch.object(ai, "_request", side_effect=recovered):
        counting.start("cam2", next_order, loader)

    next_session = AiCountingSession.objects.get(order=next_order)
    assert calls == [
        ("GET", "/processors/cam2", None),
        ("DELETE", "/processors/cam2", {"session_id": active_session.pk}),
        (
            "POST",
            "/processors/cam2",
            {
                "source": "sub",
                "session_id": next_session.pk,
                "require_continuous": True,
                "expected_analytics_scope": ANALYTICS_SCOPE_SHIPPING,
            },
        ),
    ]
    active_session.refresh_from_db()
    assert active_session.error == ""


def test_scoped_cleanup_clears_only_the_confirmed_session_marker(
    loader,
    loading_order,
):
    first = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        error=f"{counting.CLEANUP_PENDING_PREFIX}first",
    )
    second = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        error=f"{counting.CLEANUP_PENDING_PREFIX}second",
    )

    with (
        patch.object(
            ai,
            "delete",
            return_value=durable_stop_response(first.pk),
        ) as delete,
        transaction.atomic(),
    ):
        counting._finish_pending_cleanup(
            "cam2",
            cleanup_session_id=first.pk,
            known_session_worker=True,
        )

    delete.assert_called_once_with("cam2", session_id=first.pk)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.error == ""
    assert second.error == f"{counting.CLEANUP_PENDING_PREFIX}second"


def test_missing_worker_never_clears_cleanup_marker_and_recovery_retries_exact_id(
    loader,
    loading_order,
):
    stale = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        error=f"{counting.CLEANUP_PENDING_PREFIX}ambiguous 404",
    )

    with (
        patch.object(ai, "status", return_value=None),
        patch.object(ai, "delete") as delete,
        pytest.raises(ai.AiError) as exc,
        transaction.atomic(),
    ):
        counting._finish_pending_cleanup("cam2")

    assert exc.value.status == 503
    delete.assert_not_called()
    stale.refresh_from_db()
    assert stale.error.startswith(counting.CLEANUP_PENDING_PREFIX)

    with (
        patch.object(
            ai,
            "status",
            return_value={**RUNNING, "mode": "always_on"},
        ),
        patch.object(
            ai,
            "delete",
            return_value=durable_stop_response(stale.pk),
        ) as delete,
        transaction.atomic(),
    ):
        counting._finish_pending_cleanup("cam2")

    delete.assert_called_once_with("cam2", session_id=stale.pk)
    stale.refresh_from_db()
    assert stale.error == ""


def test_stale_cleanup_never_deletes_or_clears_a_different_live_session(
    loader,
    loading_order,
):
    stale = AiCountingSession.objects.create(
        order=loading_order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        error=f"{counting.CLEANUP_PENDING_PREFIX}old worker",
    )
    live_session_id = stale.pk + 100

    with (
        patch.object(
            ai,
            "status",
            return_value={
                **RUNNING,
                "mode": "session",
                "session_id": live_session_id,
            },
        ),
        patch.object(ai, "delete") as delete,
        pytest.raises(ai.AiError) as exc,
        transaction.atomic(),
    ):
        counting._finish_pending_cleanup(
            "cam2",
            cleanup_session_id=stale.pk,
        )

    assert exc.value.status == 409
    delete.assert_not_called()
    stale.refresh_from_db()
    assert stale.error == f"{counting.CLEANUP_PENDING_PREFIX}old worker"


def test_starting_session_cannot_be_reported_as_completed_order(loader):
    order = _order("pending", status="confirmed")
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=loader,
    )

    with (
        patch.object(ai, "_request") as request,
        pytest.raises(ai.AiError) as exc,
    ):
        counting.stop("cam2", order, loader, complete_order=True)

    assert exc.value.status == 409
    request.assert_not_called()
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.STARTING
    assert order.status == "confirmed"


def test_failed_business_completion_retries_the_same_durable_final(loader):
    order = _order("no-shipment", status="loading", loading_camera="cam2")
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        last_status={"total": 0},
    )

    durable = durable_stop_response(session.pk, total=47)
    with patch.object(ai, "_request", return_value=(200, durable)) as request:
        with pytest.raises(ValidationError):
            counting.stop("cam2", order, loader, complete_order=True)

        session.refresh_from_db()
        order.refresh_from_db()
        assert session.status == AiCountingSession.ACTIVE
        assert session.final_total is None
        assert order.status == "loading"

        # The first DELETE already froze the result remotely. After repairing
        # the business precondition, the retry replays that exact response.
        Shipment.objects.create(order=order, loading_started_at=timezone.now())
        completed = counting.stop("cam2", order, loader, complete_order=True)

    assert completed["bags_loaded"] == 47
    assert [call.args for call in request.call_args_list] == [
        ("DELETE", "/processors/cam2", {"session_id": session.pk}),
        ("DELETE", "/processors/cam2", {"session_id": session.pk}),
    ]
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED
    assert session.final_total == 47
    assert order.status == "loaded"


def test_complete_retry_after_committed_response_loss_is_idempotent(
    loader,
):
    order = _order("complete-retry", status="loaded")
    Shipment.objects.create(order=order, bags_loaded=31)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.CLOSED,
        started_by=loader,
        closed_by=loader,
        ended_at=timezone.now(),
        final_total=31,
        last_status={"total": 31, "session_id": 1},
    )

    with patch.object(ai, "_request") as request:
        result = counting.stop(
            "cam2",
            order,
            loader,
            complete_order=True,
            expected_session_id=session.pk,
        )

    assert result["order_status"] == "loaded"
    assert result["bags_loaded"] == 31
    assert result["session_id"] == session.pk
    assert result["total"] == 31
    request.assert_not_called()


def test_complete_order_fails_closed_without_exact_durable_final(
    loader,
):
    product = Product.objects.create(
        name="AI fallback",
        color="Blue",
        weight_kg="50",
    )
    order = _order("always", status="loading", loading_camera="cam2")
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
    always_on = {
        **RUNNING,
        "total": 999,
        "mode": "always_on",
        "recording": False,
    }

    with (
        patch.object(ai, "_request", return_value=(200, always_on)) as request,
        pytest.raises(ai.AiError) as exc,
    ):
        counting.stop("cam2", order, loader, complete_order=True)

    assert exc.value.status == 503
    assert "точный финальный счёт" in exc.value.detail
    request.assert_called_once_with(
        "DELETE",
        "/processors/cam2",
        {"session_id": session.pk},
    )
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert session.final_total is None
    assert order.status == "loading"
    assert order.shipment.bags_loaded == 0
    assert order.shipment.shipped_at is None


@pytest.mark.parametrize(
    "final_updates",
    [
        {"session_id": 999_999},
        {"continuous_analytics": False},
        {"analytics_scope": ANALYTICS_SCOPE_AI247},
        {"total": True},
    ],
)
def test_authoritative_final_rejects_wrong_identity_or_invalid_payload(
    active_session,
    final_updates,
):
    response = durable_stop_response(
        active_session.pk,
        final_updates=final_updates,
    )

    with (
        patch.object(ai, "delete", return_value=response) as delete,
        pytest.raises(ai.AiError) as exc,
    ):
        counting._finish_with_authoritative_final("cam2", active_session)

    assert exc.value.status == 503
    delete.assert_called_once_with("cam2", session_id=active_session.pk)


def test_only_starter_or_admin_can_recover_session(
    user_with_perms, loading_order, active_session,
):
    other_loader = user_with_perms("other-recovery", codes=["monoblock.view", "loader.confirm", "loader.trucks"])

    with (
        patch.object(ai, "_request") as request,
        pytest.raises(PermissionDenied) as exc,
    ):
        counting.start("cam2", loading_order, other_loader)

    assert "начавший" in str(exc.value.detail)
    request.assert_not_called()


def test_open_sessions_list_contains_camera_and_total(
    api_client, loader, user_with_perms, loading_order,
):
    viewer = user_with_perms("session-viewer", codes=["monoblock.view"])
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
        started_by=loader, last_status={"total": 17},
    )

    api_client.force_authenticate(viewer)
    resp = api_client.get("/api/cameras/ai/sessions/")
    assert resp.status_code == 200
    assert resp.data[0]["camera"] == "cam2"
    assert resp.data[0]["order_id"] == loading_order.pk
    assert resp.data[0]["last_status"]["total"] == 17


def test_open_sessions_require_monoblock_view(api_client, make_user):
    api_client.force_authenticate(make_user("session-plain-staff"))

    response = api_client.get("/api/cameras/ai/sessions/")

    assert response.status_code == 403


@pytest.mark.parametrize(
    "transport_type,number", [("train", "00123456"), ("truck", "123ABC02")]
)
def test_session_dtos_identify_transport_without_changing_number(
    api_client, user_with_perms, loading_order, transport_type, number,
):
    viewer = user_with_perms("transport-viewer", codes=["monoblock.view"])
    loading_order.transport_type = transport_type
    loading_order.truck_number = number
    loading_order.save(update_fields=["transport_type", "truck_number"])
    session = AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE,
    )
    api_client.force_authenticate(viewer)

    response = api_client.get("/api/cameras/ai/sessions/")

    assert response.status_code == 200
    assert response.data[0]["id"] == session.pk
    assert response.data[0]["order_transport_type"] == transport_type


def test_open_sessions_include_every_department(api_client, loader):
    order = _order("3", department="field", status="arrived")
    AiCountingSession.objects.create(
        order=order, camera="cam3", status=AiCountingSession.ACTIVE,
        started_by=loader,
    )
    api_client.force_authenticate(loader)

    response = api_client.get("/api/cameras/ai/sessions/")

    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["order_id"] == order.id


def test_limit_409_passes_through_and_releases_slot(loader, loading_order):
    def fake(method, path, body=None):
        return 409, {"detail": "лимит камер"}

    with (
        patch.object(ai, "_request", side_effect=fake),
        pytest.raises(ai.AiError) as exc,
    ):
        counting.start("cam2", loading_order, loader)
    assert exc.value.status == 409
    assert "лимит" in exc.value.detail
    assert not AiCountingSession.objects.filter(
        status__in=AiCountingSession.OPEN_STATUSES
    ).exists()


def test_other_order_cannot_start_until_owner_finishes(
    loader, loading_order, active_session, second_loading_order,
):
    with (
        patch.object(ai, "_request") as request,
        pytest.raises(sessions.AiSessionBusy) as exc,
    ):
        counting.start("cam2", second_loading_order, loader)
    assert exc.value.session.order_id == loading_order.pk
    request.assert_not_called()


def test_parallel_sessions_on_different_cameras(
    api_client, loader, loading_order, second_loading_order,
):
    """Две погрузки идут одновременно на разных камерах — обе стартуют."""
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
    first, created = sessions.reserve(loading_order, "cam2", loader)
    assert created is True
    with pytest.raises(sessions.AiSessionBusy) as exc:
        sessions.reserve(loading_order, "cam3", loader)
    assert exc.value.session.pk == first.pk
    assert AiCountingSession.objects.filter(
        order=loading_order, status__in=AiCountingSession.OPEN_STATUSES
    ).count() == 1


def test_current_for_camera_isolates_cameras(loader, loading_order, second_loading_order):
    AiCountingSession.objects.create(
        order=loading_order, camera="cam2", status=AiCountingSession.ACTIVE, started_by=loader)
    AiCountingSession.objects.create(
        order=second_loading_order, camera="cam3", status=AiCountingSession.ACTIVE, started_by=loader)
    assert sessions.current_for_camera("cam2").order_id == loading_order.pk
    assert sessions.current_for_camera("cam3").order_id == second_loading_order.pk
    assert sessions.current_for_camera("cam9") is None


def test_arch_motion_and_zone_helpers_address_the_camera_pc_arch_endpoints():
    with patch.object(
        ai, "_call", return_value={"state": "still", "still_seconds": 12.0}
    ) as call:
        assert ai.arch_motion("cam8") == {"state": "still", "still_seconds": 12.0}
        assert ai.arch_zone("cam8") == {"state": "still", "still_seconds": 12.0}
    assert call.call_args_list[0].args == ("GET", "/cameras/cam8/arch-motion")
    assert call.call_args_list[0].kwargs == {
        "timeout_seconds": ai.VEHICLE_RUNTIME_PROBE_TIMEOUT
    }
    assert call.call_args_list[1].args == ("GET", "/cameras/cam8/arch-zone")
    assert call.call_args_list[1].kwargs == {
        "timeout_seconds": ai.VEHICLE_RUNTIME_PROBE_TIMEOUT
    }
    with patch.object(ai, "_request", return_value=(200, {"ok": True})) as request:
        assert ai.save_arch_zone("cam8", {"points": []}) == (200, {"ok": True})
    assert request.call_args.args == ("PUT", "/cameras/cam8/arch-zone", {"points": []})
    assert request.call_args.kwargs == {"timeout_seconds": ai.VEHICLE_RUNTIME_PROBE_TIMEOUT}
