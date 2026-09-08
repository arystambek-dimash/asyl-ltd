"""Arrival/quiet/return/outage sequences through real ownership and completion."""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from apps.cameras import ai, shipping_completion as completion
from apps.cameras.models import AiCountingSession, ShippingTransportState
from apps.cameras.tests.test_shipping_automation import (
    OTHER_NUMBER, _binding, _confirm, _order, _poll, pipeline as acquisition_pipeline,  # noqa: F401
)
from apps.cameras.tests.test_shipping_tracking import body_tracking

pytestmark = pytest.mark.django_db


@pytest.fixture
def loading(request, monkeypatch):
    pipeline = request.getfixturevalue("acquisition_pipeline")
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get(order=order)
    pipeline.live[binding.conveyor_camera]["total"] = 23
    epoch = pipeline.clock.now
    receipts = {}

    def finish(camera, session_id, guard):
        if session_id in receipts:
            return receipts[session_id]
        if guard.get("recovery_only"):
            raise ai.AiError(409, "No receipt", {"code": "auto_finish_not_completed"})
        live = pipeline.live[camera]
        proof = {
            "schema_version": 1,
            "activity_generation": guard["activity_generation"],
            "min_clear_seconds": 40,
            "completed_at": pipeline.clock.now.isoformat(),
            "cargo_activity": live["cargo_activity"],
        }
        final = {**live, "running": False, "automatic_finish": proof}
        receipts[session_id] = {"ok": True, "cam": camera, "stopped": True, "final": final}
        del pipeline.live[camera]
        return receipts[session_id]

    finish_mock = Mock(side_effect=finish)
    monkeypatch.setattr(ai, "finish_automatic", finish_mock)

    def step(*, presence="absent", cargo="clear", advance=2, error=None, generation="processor-1", number=None):
        now = pipeline.clock.now + timedelta(seconds=advance)
        if binding.conveyor_camera in pipeline.live:
            pipeline.live[binding.conveyor_camera]["cargo_activity"] = {
                "schema_version": 1, "basis": "bag_detections_and_scene_motion",
                "state": cargo, "observed_at": now.isoformat(),
                "clear_since": (epoch - timedelta(seconds=50)).isoformat() if cargo == "clear" else None,
                "last_activity_at": None, "generation": generation,
                "sequence": pipeline.serial + 1, "reason": "observed",
            }
        tracking = body_tracking(now, visit_id="visit-1")
        if presence != "present":
            tracking.update(
                presence=presence, motion="unknown", number_associated=False,
                detection_count=0, stationary_since=None, present_since=None,
                last_seen_at=epoch.isoformat(),
                absent_since=(epoch - timedelta(seconds=6)).isoformat() if presence == "absent" else None,
            )
        return _poll(pipeline, binding, number=number, tracking=tracking, advance=advance, error=error)

    return pipeline, binding, order, session, step, finish_mock, finish


def test_presence_keeps_loading_even_with_long_empty_conveyor(loading):
    pipeline, _, order, _, step, finish, _ = loading
    for _ in range(30):
        step(presence="present")
    order.refresh_from_db()
    assert order.status == "loading"
    finish.assert_not_called()
    assert pipeline.start.call_count == 1


def test_exact_40_seconds_absence_and_idle_freezes_count_into_same_order(loading):
    pipeline, binding, order, session, step, finish, _ = loading
    step()
    for _ in range(19):
        step()
    state = ShippingTransportState.objects.get(binding=binding)
    assert state.auto_finish["remaining_seconds"] == 2
    finish.assert_not_called()
    step()
    order.refresh_from_db()
    session.refresh_from_db()
    state.refresh_from_db()
    assert order.status == "loaded"
    assert order.loading_camera == ""
    assert order.shipment.bags_loaded == session.final_total == 23
    assert order.shipment.shipped_at is None
    assert session.status == AiCountingSession.CLOSED
    assert state.auto_finish["state"] == "completed"
    assert session.last_status["automatic_finish"]["cargo_activity"]["state"] == "clear"
    assert pipeline.start.call_count == 1
    pipeline.delete.assert_not_called()
    pipeline.reset.assert_not_called()
    finish.assert_called_once()


def test_next_vehicle_can_acquire_immediately_after_verified_auto_completion(loading):
    pipeline, binding, order, _, step, _, _ = loading
    next_order = _order(pipeline, number=OTHER_NUMBER)
    for _ in range(21):
        step()
    # No additional empty-zone frame after closure; a new stopped vehicle is
    # already there on the next observation. It still needs its three votes.
    _confirm(pipeline, binding, number=OTHER_NUMBER, visit_id="visit-2")
    next_order.refresh_from_db()
    order.refresh_from_db()
    assert next_order.status == "loading"
    assert order.status == "loaded"
    assert order.shipment.bags_loaded == 23
    assert AiCountingSession.objects.get(order=next_order).camera == binding.conveyor_camera


@pytest.mark.parametrize("disruption", ["present", "unknown", "cargo", "error", "gap", "restart"])
def test_any_interruption_requires_a_new_full_quiet_interval(loading, disruption):
    _, binding, order, _, step, finish, _ = loading
    for _ in range(19):
        step()
    kwargs = {
        "present": {"presence": "present"},
        "unknown": {"presence": "unknown"},
        "cargo": {"cargo": "active"},
        "error": {"error": ai.AiUnavailable("camera offline")},
        "gap": {"advance": 45},
        "restart": {"generation": "processor-2"},
    }[disruption]
    step(**kwargs)
    step()
    for _ in range(18):
        step()
    finish.assert_not_called()
    order.refresh_from_db()
    assert order.status == "loading"
    assert ShippingTransportState.objects.get(binding=binding).auto_finish["state"] == "waiting"


def test_missing_cargo_contract_never_treats_unchanged_total_as_idle(loading):
    pipeline, binding, order, _, _, finish, _ = loading
    for _ in range(25):
        _poll(pipeline, binding, presence="absent", number=None)
    order.refresh_from_db()
    assert order.status == "loading"
    finish.assert_not_called()
    assert ShippingTransportState.objects.get(binding=binding).auto_finish["state"] == "blocked"


def test_lost_finish_ack_recovers_receipt_even_when_vehicle_returns(loading):
    pipeline, binding, order, session, step, finish, finish_impl = loading

    def lost_ack(camera, session_id, guard):
        finish_impl(camera, session_id, guard)
        raise ai.AiUnavailable("response lost after durable freeze")

    finish.side_effect = lost_ack
    for _ in range(21):
        step()
    order.refresh_from_db()
    assert order.status == "loading"
    assert ShippingTransportState.objects.get(binding=binding).auto_finish["state"] == "finishing"
    assert binding.conveyor_camera not in pipeline.live
    finish.side_effect = finish_impl
    step(presence="present")
    order.refresh_from_db()
    session.refresh_from_db()
    assert order.status == "loaded"
    assert session.final_total == 23
    assert finish.call_args.args[2]["recovery_only"] is True
    assert pipeline.start.call_count == 1


def test_request_lost_before_freeze_does_not_finish_returned_vehicle(loading):
    pipeline, binding, order, _, step, finish, finish_impl = loading
    finish.side_effect = ai.AiUnavailable("request lost before reaching camera PC")
    for _ in range(21):
        step()
    finish.side_effect = finish_impl
    step(presence="present")
    order.refresh_from_db()
    assert order.status == "loading"
    assert ShippingTransportState.objects.get(binding=binding).auto_finish["state"] == "blocked"
    assert binding.conveyor_camera in pipeline.live
    assert finish.call_args.args[2]["recovery_only"] is True
    step(presence="present")
    assert pipeline.start.call_count == 1


def test_explicit_terminal_cancellation_supersedes_pending_automatic_intent(loading):
    pipeline, binding, order, session, step, finish, _ = loading
    finish.side_effect = ai.AiUnavailable("request lost")
    for _ in range(21):
        step()
    session.refresh_from_db()
    session.status = AiCountingSession.CLOSED
    session.ended_at = timezone.now()
    session.save()
    order.refresh_from_db()
    order.status = "confirmed"
    order.loading_camera = ""
    order.save()
    step(presence="present")
    assert finish.call_count == 1
    assert ShippingTransportState.objects.get(binding=binding).auto_finish["state"] == "blocked"
    order.refresh_from_db()
    assert order.status == "confirmed"
    # The operator also cleared the old remote boundary. A new vehicle still
    # needs fresh stationary number votes, but no extra empty frame is needed.
    pipeline.live.pop(binding.conveyor_camera)
    next_order = _order(pipeline, number=OTHER_NUMBER)
    _confirm(pipeline, binding, number=OTHER_NUMBER, visit_id="visit-2")
    next_order.refresh_from_db()
    assert next_order.status == "loading"


def test_guard_rejection_preserves_session_and_restarts_countdown(loading):
    _, binding, order, _, step, finish, _ = loading
    finish.side_effect = ai.AiError(409, "new bag arrived", {"code": "auto_finish_guard_rejected"})
    for _ in range(21):
        step()
    order.refresh_from_db()
    assert order.status == "loading"
    assert ShippingTransportState.objects.get(binding=binding).auto_finish["state"] == "blocked"
    step()
    assert ShippingTransportState.objects.get(binding=binding).auto_finish["remaining_seconds"] == 40


@pytest.mark.parametrize("change", [
    {"basis": "count_unchanged"}, {"schema_version": True}, {"sequence": True},
    {"observed_at": None}, {"clear_since": None}, {"generation": ""},
    {"observed_at": "2020-01-01T00:00:00Z"}, {"clear_since": "2099-01-01T00:00:00Z"},
])
def test_invalid_cargo_evidence_cannot_authorize_completion(loading, change):
    pipeline, binding, _, session, step, _, _ = loading
    step()
    live = pipeline.live[binding.conveyor_camera]
    live["cargo_activity"].update(change)
    assert completion.cargo_observation(live, session.pk) is None


def test_stale_api_countdown_is_blocked_without_changing_persisted_state():
    raw = completion.status("waiting", "Waiting", now=timezone.now() - timedelta(seconds=20), remaining_seconds=1)
    assert completion.public_status(raw)["state"] == "blocked"
    assert raw["state"] == "waiting"
