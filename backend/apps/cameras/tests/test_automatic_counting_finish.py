"""Guarded automatic completion with real DB workflow and mocked camera HTTP."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.cameras import ai, counting
from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order
from apps.shipments.models import Shipment
from apps.shipments.services import finish_ai_counting

pytestmark = pytest.mark.django_db


@pytest.fixture
def completion(monkeypatch):
    now = timezone.now()
    client = Client.objects.create_with_user(
        first_name="Automatic", last_name="Finish", phone="auto-finish"
    )
    order = Order.objects.create(
        client=client, status="loading", loading_camera="cam2", truck_number="123ABC01"
    )
    shipment = Shipment.objects.create(order=order, loading_started_at=now)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        automatically_started=True,
        activated_at=now - timedelta(minutes=5),
        last_status={"total": 11},
    )
    guard = {
        "schema_version": 1,
        "activity_generation": "test-generation",
        "min_clear_seconds": 40,
    }
    activity = {
        "schema_version": 1,
        "basis": "bag_detections_and_scene_motion",
        "state": "clear",
        "observed_at": now.isoformat(),
        "clear_since": (now - timedelta(seconds=45)).isoformat(),
        "last_activity_at": (now - timedelta(seconds=46)).isoformat(),
        "generation": guard["activity_generation"],
        "sequence": 28,
        "reason": "clear",
    }
    proof = {
        **guard,
        "completed_at": now.isoformat(),
        "cargo_activity": activity,
    }
    final = {
        "cam": "cam2",
        "mode": "session",
        "running": False,
        "session_id": session.pk,
        "continuous_analytics": True,
        "analytics_scope": "shipping",
        "total": 13,
        "stream": "cam2ai",
        "automatic_finish": proof,
    }
    receipt = {
        "ok": True,
        "stopped": True,
        "cam": "cam2",
        "session_id": session.pk,
        "mode": "always_on",
        "final": final,
        "automatic_finish": proof,
        "processor": {"cam": "cam2", "running": True, "mode": "always_on"},
    }
    request = Mock(return_value=(200, receipt))
    monkeypatch.setattr(ai, "_request", request)
    return SimpleNamespace(
        order=order,
        shipment=shipment,
        session=session,
        guard=guard,
        receipt=receipt,
        request=request,
        now=now,
    )


def _complete(fixture, **kwargs):
    return counting.complete_automatic(
        "cam2",
        fixture.order,
        expected_session_id=fixture.session.pk,
        guard=kwargs.pop("guard", fixture.guard),
        **kwargs,
    )


@pytest.mark.parametrize("transport", ["truck", "train"])
def test_automatic_finish_keeps_exact_final_and_only_completes_loading(
    completion, transport
):
    completion.order.transport_type = transport
    completion.order.save(update_fields=["transport_type"])
    result = _complete(completion)
    completion.session.refresh_from_db()
    completion.order.refresh_from_db()
    completion.shipment.refresh_from_db()
    assert result["bags_loaded"] == result["total"] == 13
    assert completion.session.status == AiCountingSession.CLOSED
    assert completion.session.final_total == 13
    assert completion.session.closed_by is None
    assert (
        completion.session.last_status["automatic_finish"]["cargo_activity"]["state"]
        == "clear"
    )
    assert completion.session.last_status["auto_finish"]["state"] == "completed"
    assert completion.order.status == "loaded" and completion.order.loading_camera == ""
    assert completion.shipment.bags_loaded == 13
    assert completion.shipment.shipped_at is None
    assert completion.order.is_debt is False
    event = EventLog.objects.get(order=completion.order, event_type="loading_done")
    assert event.user is None
    assert event.payload["source"] == "ai_final_automatic"
    assert event.payload["session_id"] == completion.session.pk
    assert not EventLog.objects.filter(
        order=completion.order, event_type="shipment"
    ).exists()
    completion.request.assert_called_once_with(
        "POST",
        "/processors/cam2/finish-automatic",
        {"session_id": completion.session.pk, "automatic_guard": completion.guard},
    )


def test_system_cannot_finish_manually_started_session(completion):
    completion.session.automatically_started = False
    completion.session.save(update_fields=["automatically_started"])
    with pytest.raises(PermissionDenied):
        _complete(completion)
    completion.request.assert_not_called()


@pytest.mark.parametrize("mismatch", ["session", "camera", "order"])
def test_wrong_identity_never_finishes_another_order(completion, mismatch):
    camera, order, session_id = "cam2", completion.order, completion.session.pk
    if mismatch == "session":
        session_id += 100
    elif mismatch == "camera":
        camera = "cam3"
    else:
        order = Order.objects.create(client=order.client, status="confirmed")
    with pytest.raises(ai.AiError) as error:
        counting.complete_automatic(
            camera, order, expected_session_id=session_id, guard=completion.guard
        )
    assert error.value.status == 409
    completion.request.assert_not_called()


def test_starting_reservation_cannot_be_finished(completion):
    completion.session.status = AiCountingSession.STARTING
    completion.session.save(update_fields=["status"])
    with pytest.raises(ai.AiError) as error:
        _complete(completion)
    assert error.value.status == 409
    completion.request.assert_not_called()


def test_final_database_fence_runs_before_camera_mutation(completion):
    fence = Mock(side_effect=ai.AiUnavailable("Fresh absence no longer confirmed"))
    with pytest.raises(ai.AiUnavailable):
        _complete(completion, before_remote_finish=fence)
    fence.assert_called_once()
    completion.request.assert_not_called()
    completion.session.refresh_from_db()
    assert completion.session.status == AiCountingSession.ACTIVE


@pytest.mark.parametrize(
    "code", ["auto_finish_guard_rejected", "auto_finish_not_completed"]
)
def test_explicit_cv_guard_rejection_preserves_loading_and_error_code(completion, code):
    completion.request.return_value = (
        409,
        {"code": code, "detail": "Activity changed"},
    )
    with pytest.raises(ai.AiError) as error:
        _complete(completion, guard={**completion.guard, "recovery_only": True})
    assert error.value.status == 409
    assert error.value.payload["code"] == code
    completion.session.refresh_from_db()
    completion.order.refresh_from_db()
    assert completion.session.status == AiCountingSession.ACTIVE
    assert completion.session.final_total is None
    assert completion.order.status == "loading"


def test_lost_ack_recovery_replays_same_final_after_current_activity_changes(
    completion,
):
    completion.request.side_effect = [
        ai.AiUnavailable("ACK lost"),
        (200, completion.receipt),
    ]
    with pytest.raises(ai.AiUnavailable):
        _complete(completion)
    completion.session.refresh_from_db()
    assert completion.session.status == AiCountingSession.ACTIVE
    recovery = {**completion.guard, "recovery_only": True}
    result = _complete(completion, guard=recovery)
    assert result["total"] == 13
    assert completion.request.call_args_list[-1].args[2]["automatic_guard"] == recovery
    assert (
        EventLog.objects.filter(
            order=completion.order, event_type="loading_done"
        ).count()
        == 1
    )


def test_business_rollback_retries_exact_durable_final(completion, monkeypatch):
    original = counting.finish_ai_counting
    failures = 0

    def fail_after_writing(*args, **kwargs):
        nonlocal failures
        shipment = original(*args, **kwargs)
        if failures == 0:
            failures += 1
            raise ValidationError("simulated business rollback")
        return shipment

    monkeypatch.setattr(counting, "finish_ai_counting", fail_after_writing)
    with pytest.raises(ValidationError):
        _complete(completion)
    completion.session.refresh_from_db()
    completion.order.refresh_from_db()
    completion.shipment.refresh_from_db()
    assert completion.session.status == AiCountingSession.ACTIVE
    assert completion.shipment.bags_loaded == 0
    assert completion.order.status == "loading"
    assert not EventLog.objects.filter(
        order=completion.order, event_type="loading_done"
    ).exists()
    assert (
        _complete(completion, guard={**completion.guard, "recovery_only": True})[
            "total"
        ]
        == 13
    )
    assert completion.request.call_count == 2


def test_committed_retry_does_not_touch_a_new_session_on_same_camera(completion):
    _complete(completion)
    next_order = Order.objects.create(
        client=completion.order.client, status="loading", loading_camera="cam2"
    )
    next_session = AiCountingSession.objects.create(
        order=next_order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        automatically_started=True,
    )
    completion.request.reset_mock()
    result = _complete(completion, guard={**completion.guard, "recovery_only": True})
    assert result["total"] == 13 and result["session_id"] == completion.session.pk
    next_session.refresh_from_db()
    assert next_session.status == AiCountingSession.ACTIVE
    completion.request.assert_not_called()
    assert (
        EventLog.objects.filter(
            order=completion.order, event_type="loading_done"
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    "malformed",
    [
        "identity",
        "total",
        "missing_proof",
        "active",
        "generation",
        "recent",
        "stale",
        "activity_after_clear",
        "missing_sequence",
    ],
)
def test_invalid_guarded_final_never_completes_business_order(completion, malformed):
    receipt = deepcopy(completion.receipt)
    final = receipt["final"]
    if malformed == "identity":
        final["session_id"] += 100
    elif malformed == "total":
        final["total"] = "13"
    elif malformed == "missing_proof":
        del final["automatic_finish"]
    elif malformed == "active":
        final["automatic_finish"]["cargo_activity"]["state"] = "active"
    elif malformed == "generation":
        final["automatic_finish"]["activity_generation"] = "another-generation"
    elif malformed == "recent":
        final["automatic_finish"]["cargo_activity"]["clear_since"] = (
            completion.now - timedelta(seconds=39)
        ).isoformat()
    elif malformed == "stale":
        final["automatic_finish"]["cargo_activity"]["observed_at"] = (
            completion.now - timedelta(seconds=16)
        ).isoformat()
    elif malformed == "activity_after_clear":
        final["automatic_finish"]["cargo_activity"]["last_activity_at"] = (
            completion.now - timedelta(seconds=1)
        ).isoformat()
    elif malformed == "missing_sequence":
        del final["automatic_finish"]["cargo_activity"]["sequence"]
    completion.request.return_value = (200, receipt)
    with pytest.raises(ai.AiError) as error:
        _complete(completion)
    assert error.value.status == 503
    completion.session.refresh_from_db()
    completion.order.refresh_from_db()
    assert completion.session.status == AiCountingSession.ACTIVE
    assert completion.session.final_total is None
    assert completion.order.status == "loading"


@pytest.mark.parametrize("total", [None, True, "13", -1, 2_147_483_648])
def test_automatic_shipment_completion_never_uses_ordered_quantity_fallback(
    completion, total
):
    with pytest.raises(ValidationError):
        finish_ai_counting(
            completion.order, total, None, automatic_session_id=completion.session.pk
        )
    completion.shipment.refresh_from_db()
    assert completion.shipment.bags_loaded == 0
