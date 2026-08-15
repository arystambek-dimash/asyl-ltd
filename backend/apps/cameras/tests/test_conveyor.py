"""Order target snapshots and the fail-safe conveyor API contract."""

from unittest.mock import patch

import pytest
from rest_framework.exceptions import ValidationError

from apps.cameras import ai
from apps.cameras.models import AiCountingSession, MonoblockCameraSettings
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.orders.services import replace_items
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def camera_control(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    MonoblockCameraSettings.objects.create(camera_sources=["cam2"])


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("conveyor-loader", codes=["shipping.load"])


def _order(*, status="confirmed", bags=12):
    client = Client.objects.create_with_user(
        first_name="Conveyor", last_name="Order", phone=f"conv-{status}-{bags}"
    )
    order = Order.objects.create(
        client=client,
        status=status,
        truck_number="01CONV",
        loading_camera="cam2" if status == "loading" else "",
    )
    if bags:
        OrderItem.objects.create(order=order, quantity=bags)
    return order


def _running(
    *,
    total=0,
    state="running",
    feedback=1,
    session_id=1,
    target_total=10,
    configured=True,
):
    return {
        "cam": "cam2",
        "running": True,
        "mode": "session",
        "stream": "cam2ai",
        "total": total,
        "session_id": session_id,
        "target_total": target_total,
        "conveyor": {
            "configured": configured,
            "enabled": configured,
            "session_id": session_id,
            "target_total": target_total,
            "state": state,
            "desired": 1 if state == "running" else 0,
            "feedback": feedback,
            "online": True,
        },
    }


def test_bind_freezes_target_and_uses_edge_supervised_start(auth_client, loader):
    order = _order(bags=17)
    api_client = auth_client(loader)

    def edge(method, path, body=None):
        if path.endswith("/session"):
            return 200, _running(
                state="armed",
                feedback=0,
                session_id=body["session_id"],
                target_total=body["target_total"],
            )
        return 200, _running(
            session_id=body["session_id"],
            target_total=17,
        )

    with patch.object(ai, "_request", side_effect=edge) as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/", {"order_id": order.pk}, format="json"
        )

    assert response.status_code == 200, response.data
    session = AiCountingSession.objects.get(order=order)
    assert session.target_total == 17
    assert session.conveyor_enabled is True
    assert response.data["target_total"] == 17
    assert response.data["remaining"] == 17
    assert response.data["conveyor"]["feedback"] == 1
    assert request.call_args_list[0].args == (
        "POST",
        "/processors/cam2/session",
        {"session_id": session.pk, "target_total": 17},
    )
    assert request.call_args_list[1].args == (
        "POST",
        "/processors/cam2/conveyor/start",
        {"session_id": session.pk},
    )


def test_old_camera_service_falls_back_without_claiming_conveyor(
    auth_client, loader,
):
    order = _order(bags=8)
    api_client = auth_client(loader)
    legacy = {"cam": "cam2", "running": True, "mode": "session", "total": 0}

    with patch.object(
        ai,
        "_request",
        side_effect=[
            (404, {"detail": "not found"}),
            (200, legacy),
            (200, legacy),
        ],
    ) as request:
        response = api_client.post(
            "/api/cameras/cam2/ai/", {"order_id": order.pk}, format="json"
        )

    assert response.status_code == 200
    session = AiCountingSession.objects.get(order=order)
    assert session.target_total == 8
    assert session.conveyor_enabled is False
    assert response.data["conveyor"]["state"] == "unconfigured"
    assert [call.args[:2] for call in request.call_args_list] == [
        ("POST", "/processors/cam2/session"),
        ("POST", "/processors/cam2"),
        ("POST", "/processors/cam2/reset"),
    ]


def test_ambiguous_on_response_happens_only_after_loading_is_durable(
    auth_client, loader,
):
    order = _order(bags=9)
    session_id = None

    def edge(method, path, body=None):
        nonlocal session_id
        if path.endswith("/session"):
            session_id = body["session_id"]
            return 200, _running(
                state="armed",
                feedback=0,
                session_id=session_id,
                target_total=9,
            )
        if path.endswith("/conveyor/start"):
            raise ai.AiUnavailable("lost ON response")
        return 200, _running(
            state="off",
            feedback=0,
            session_id=session_id,
            target_total=9,
        )

    with patch.object(ai, "_request", side_effect=edge):
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/", {"order_id": order.pk}, format="json"
        )

    assert response.status_code == 502
    session = AiCountingSession.objects.get(order=order)
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert session.conveyor_enabled is True
    assert order.status == "loading"
    assert order.loading_camera == "cam2"


def test_goal_is_derived_with_greater_than_or_equal_semantics(auth_client, loader):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )

    with patch.object(
        ai,
        "_request",
        return_value=(
            200,
            _running(
                total=11,
                state="goal_reached",
                feedback=0,
                session_id=session.pk,
                target_total=10,
            ),
        ),
    ):
        response = auth_client(loader).get(
            f"/api/cameras/cam2/ai/?order_id={order.pk}"
        )

    assert response.status_code == 200
    assert response.data["session_id"] == session.pk
    assert response.data["goal_reached"] is True
    assert response.data["remaining"] == 0


def test_emergency_stop_does_not_close_or_complete_session(auth_client, loader):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )
    stopped = _running(
        total=6,
        state="off",
        feedback=0,
        session_id=session.pk,
        target_total=10,
    )

    with patch.object(ai, "_request", return_value=(200, stopped)) as request:
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/conveyor/stop/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 200
    request.assert_called_once_with(
        "POST",
        "/processors/cam2/conveyor/emergency-stop",
        {"session_id": session.pk},
    )
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"
    assert response.data["conveyor"]["feedback"] == 0


def test_unconfirmed_off_blocks_completion_and_keeps_ownership(
    auth_client, loader,
):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )

    goal = _running(
        total=10,
        state="goal_reached",
        feedback=0,
        session_id=session.pk,
        target_total=10,
    )
    unsafe = _running(
        total=10,
        session_id=session.pk,
        target_total=10,
    )
    with patch.object(ai, "_request", side_effect=[(200, goal), (200, unsafe)]):
        response = auth_client(loader).delete(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk, "complete_order": True},
            format="json",
        )

    assert response.status_code == 503
    assert "не подтвердил остановку" in response.data["detail"]
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"
    assert order.loading_camera == "cam2"


def test_completion_reverifies_off_before_loaded_transition(auth_client, loader):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )
    safe = _running(
        total=10,
        state="goal_reached",
        feedback=0,
        session_id=session.pk,
        target_total=10,
    )
    unsafe = _running(
        total=10,
        state="running",
        feedback=1,
        session_id=session.pk,
        target_total=10,
    )
    stop_calls = 0

    def edge(method, path, body=None):
        nonlocal stop_calls
        assert path in {
            "/processors/cam2",
            "/processors/cam2/conveyor/stop",
        }
        if method == "GET":
            return 200, safe
        stop_calls += 1
        if stop_calls == 1:
            return 200, safe
        # A preflight-only OFF proof must never permit the irreversible count
        # completion if the controller changed before the DB transition.
        return 200, unsafe

    with patch.object(ai, "_request", side_effect=edge) as request:
        response = auth_client(loader).delete(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk, "complete_order": True},
            format="json",
        )

    assert response.status_code == 503
    assert [call.args[1] for call in request.call_args_list] == [
        "/processors/cam2",
        "/processors/cam2/conveyor/stop",
        "/processors/cam2/conveyor/stop",
    ]
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"
    assert order.loading_camera == "cam2"


def test_controlled_completion_commits_fresh_final_stop_snapshot(
    auth_client,
    loader,
):
    order = _order(status="loading", bags=10)
    product = Product.objects.create(
        name="Conveyor final",
        color="White",
        weight_kg="50",
        price="100.00",
    )
    order.items.update(product=product)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )
    Shipment.objects.create(order=order, truck_number=order.truck_number)
    stopped = _running(
        total=10,
        state="goal_reached",
        feedback=0,
        session_id=session.pk,
        target_total=10,
    )

    def edge(method, path, body=None):
        if method == "DELETE":
            return 200, {"running": False}
        return 200, stopped

    with patch.object(ai, "_request", side_effect=edge) as request:
        response = auth_client(loader).delete(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk, "complete_order": True},
            format="json",
        )

    assert response.status_code == 200, response.data
    assert response.data["order_status"] == "loaded"
    assert response.data["bags_loaded"] == 10
    assert [call.args[:2] for call in request.call_args_list] == [
        ("GET", "/processors/cam2"),
        ("POST", "/processors/cam2/conveyor/stop"),
        ("POST", "/processors/cam2/conveyor/stop"),
        ("DELETE", "/processors/cam2"),
    ]
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED
    assert session.final_total == 10
    assert session.error == ""
    assert order.status == "loaded"
    assert order.loading_camera == ""
    assert order.is_debt is False
    assert order.shipment.shipped_at is None
    assert not StockItem.objects.filter(product=product).exists()


def test_unsafe_prepare_is_compensated_and_never_starts_loading(
    auth_client, loader,
):
    order = _order(bags=7)

    def edge(method, path, body=None):
        if path.endswith("/session"):
            return 200, _running(
                session_id=body["session_id"],
                target_total=7,
            )
        return 200, _running(
            state="off",
            feedback=0,
            session_id=body["session_id"],
            target_total=7,
        )

    with patch.object(ai, "_request", side_effect=edge) as request:
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/",
            {"order_id": order.pk},
            format="json",
        )

    assert response.status_code == 503
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert [call.args[1] for call in request.call_args_list] == [
        "/processors/cam2/session",
        "/processors/cam2/conveyor/emergency-stop",
    ]


def test_durable_controller_cannot_be_downgraded_by_false_response(
    auth_client, loader,
):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )
    malformed = _running(
        total=10,
        state="off",
        feedback=0,
        session_id=session.pk,
        target_total=10,
        configured=False,
    )
    malformed["conveyor"]["online"] = False

    with patch.object(ai, "_request", return_value=(200, malformed)):
        response = auth_client(loader).delete(
            "/api/cameras/cam2/ai/",
            {
                "order_id": order.pk,
                "session_id": session.pk,
                "complete_order": True,
            },
            format="json",
        )

    assert response.status_code == 503
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE


def test_completion_before_target_is_rejected_without_stopping_belt(
    auth_client, loader,
):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )
    current = _running(
        total=9,
        session_id=session.pk,
        target_total=10,
    )

    with patch.object(ai, "_request", return_value=(200, current)) as request:
        response = auth_client(loader).delete(
            "/api/cameras/cam2/ai/",
            {
                "order_id": order.pk,
                "session_id": session.pk,
                "complete_order": True,
            },
            format="json",
        )

    assert response.status_code == 409
    request.assert_called_once_with("GET", "/processors/cam2", None)
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE


def test_stale_session_fence_cannot_stop_new_session(auth_client, loader):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
    )

    with patch.object(ai, "_request") as request:
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/conveyor/stop/",
            {"order_id": order.pk, "session_id": session.pk + 1},
            format="json",
        )

    assert response.status_code == 409
    request.assert_not_called()


def test_emergency_off_is_not_blocked_by_mismatched_live_binding(
    auth_client, loader,
):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=False,
    )
    mismatched = _running(
        total=2,
        session_id=session.pk + 100,
        target_total=99,
    )
    physical_off = _running(
        total=2,
        state="off",
        feedback=0,
        session_id=session.pk + 100,
        target_total=99,
    )

    with patch.object(
        ai,
        "_request",
        side_effect=[(200, mismatched), (200, physical_off)],
    ) as request:
        response = auth_client(loader).post(
            "/api/cameras/cam2/ai/conveyor/stop/",
            {"order_id": order.pk, "session_id": session.pk},
            format="json",
        )

    assert response.status_code == 200
    assert [call.args[1] for call in request.call_args_list] == [
        "/processors/cam2",
        "/processors/cam2/conveyor/emergency-stop",
    ]
    assert response.data["conveyor"]["feedback"] == 0


def test_cancel_race_after_camera_pc_restart_latches_physical_off(
    auth_client, loader,
):
    order = _order(status="loading", bags=10)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=loader,
        target_total=10,
        conveyor_enabled=True,
        last_status={"total": 4},
    )
    physical_off = {
        "cam": "cam2",
        "running": False,
        "mode": "idle",
        "conveyor": {
            "configured": True,
            "enabled": True,
            "state": "off",
            "desired": 0,
            "feedback": 0,
            "online": True,
            "terminal": True,
            "stop_reason": "emergency_stop",
        },
    }

    def edge(method, path, body=None):
        if path.endswith("/conveyor/emergency-stop"):
            assert body == {"session_id": session.pk}
            return 200, physical_off
        assert method == "GET"
        assert path == "/processors/cam2"
        return 404, {"detail": "processor not found"}

    # Simulate DELETE reading STARTING/conveyor_enabled=False immediately
    # before the concurrent start transaction commits ACTIVE/True. The locked
    # phase must re-read the durable row and install emergency OFF before a
    # delayed post-commit start can win.
    stale_preflight = AiCountingSession.objects.get(pk=session.pk)
    stale_preflight.conveyor_enabled = False
    with (
        patch(
            "apps.cameras.counting.sessions.current_for_camera",
            return_value=stale_preflight,
        ),
        patch.object(ai, "_request", side_effect=edge) as request,
    ):
        response = auth_client(loader).delete(
            "/api/cameras/cam2/ai/",
            {
                "order_id": order.pk,
                "session_id": session.pk,
                "complete_order": False,
            },
            format="json",
        )

    assert response.status_code == 200
    assert [call.args[1] for call in request.call_args_list] == [
        "/processors/cam2/conveyor/emergency-stop",
        "/processors/cam2",
    ]
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.CLOSED
    assert session.final_total is None
    assert order.status == "loading"
    assert order.loading_camera == ""


def test_open_ai_session_freezes_order_items(loader):
    order = _order(status="confirmed", bags=10)
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=loader,
        target_total=10,
    )

    with pytest.raises(ValidationError) as exc:
        replace_items(order, [], None, loader)

    assert exc.value.detail["code"] == "ai_session_active"
