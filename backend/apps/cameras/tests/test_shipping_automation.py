"""Order acquisition integration tests with real DB/session lifecycle, mocked hardware."""

from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connections
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.cameras import ai, counting, transport_recognition
from apps.cameras import shipping_automation as automation
from apps.cameras import shipping_transport_scheduler as scheduling
from apps.cameras.models import (
    AiCountingSession,
    MonoblockCameraSettings,
    ShippingTransportCamera,
    ShippingTransportRecognitionEvent,
    ShippingTransportState,
)
from apps.cameras.policies import can_control_session, session_started_by_name
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

NUMBER = "123ABC01"
OTHER_NUMBER = "456DEF02"
SNAPSHOT = b"\xff\xd8\xffshipping-evidence-test-frame"


@pytest.fixture
def pipeline(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    clock = SimpleNamespace(now=datetime(2026, 9, 8, 8, tzinfo=datetime_timezone.utc))
    monkeypatch.setattr(timezone, "now", lambda: clock.now)
    monkeypatch.setattr(ai, "enabled", lambda: True)
    # A missed hardware stub fails immediately rather than touching camera PCs.
    request = Mock(side_effect=AssertionError("Unexpected network AI request"))
    monkeypatch.setattr(ai, "_request", request)
    monkeypatch.setattr(
        ai,
        "camera_frame_jpeg",
        Mock(side_effect=AssertionError("Unexpected go2rtc read")),
    )
    reset = Mock(
        side_effect=AssertionError("Automatic acquisition must not reset counters")
    )
    monkeypatch.setattr(ai, "reset", reset)
    observe = Mock()
    monkeypatch.setattr(transport_recognition, "observe_transport", observe)
    MonoblockCameraSettings.objects.create(camera_sources=["cam2", "cam3"])
    client = Client.objects.create_with_user(
        first_name="Shipping", last_name="Automation", phone="shipping-auto-test"
    )
    live = {}

    def start(camera, options):
        assert options == {
            "source": "sub",
            "session_id": options["session_id"],
            "require_continuous": True,
            "expected_analytics_scope": "shipping",
        }
        assert AiCountingSession.objects.get(pk=options["session_id"]).camera == camera
        result = {
            "cam": camera,
            "mode": "session",
            "source": "sub",
            "running": True,
            "session_id": options["session_id"],
            "continuous_analytics": True,
            "analytics_scope": "shipping",
            "total": 0,
        }
        live[camera] = result
        return result.copy()

    def delete(camera, *, session_id):
        current = live[camera]
        assert current["session_id"] == session_id
        final = {**current, "running": False}
        del live[camera]
        return {"ok": True, "stopped": True, "cam": camera, "final": final}

    start_mock = Mock(side_effect=start)
    status_mock = Mock(side_effect=lambda camera: live.get(camera))
    delete_mock = Mock(side_effect=delete)
    monkeypatch.setattr(ai, "start", start_mock)
    monkeypatch.setattr(ai, "status", status_mock)
    monkeypatch.setattr(ai, "delete", delete_mock)
    return SimpleNamespace(
        clock=clock,
        observe=observe,
        client=client,
        serial=0,
        start=start_mock,
        start_impl=start,
        status=status_mock,
        delete=delete_mock,
        reset=reset,
        request=request,
        live=live,
    )


def _binding(*, camera="cam2", model="vehicle_number"):
    return ShippingTransportCamera.objects.create(
        conveyor_camera=camera,
        number_camera="cam7" if camera == "cam2" else "cam8",
        recognition_model=model,
    )


def _order(pipeline, *, number=NUMBER, transport="truck", status="confirmed", **extra):
    return Order.objects.create(
        client=pipeline.client,
        truck_number=number,
        transport_type=transport,
        status=status,
        **extra,
    )


def _poll(
    pipeline,
    binding,
    *,
    number=NUMBER,
    frame_id=None,
    age=0,
    advance=2,
    error=None,
    visit_id="visit-1",
    presence="present",
    motion="stationary",
    associated=None,
    tracking=None,
):
    pipeline.serial += 1
    pipeline.clock.now += timedelta(seconds=advance)
    pipeline.observe.side_effect = error
    observed_at = pipeline.clock.now - timedelta(seconds=age)
    if tracking is None:
        tracking = {
            "schema_version": 1,
            "basis": "transport_body",
            "presence": presence,
            "motion": motion if presence == "present" else "unknown",
            "visit_id": visit_id,
            "observed_at": observed_at.isoformat(),
            "present_since": (observed_at - timedelta(seconds=10)).isoformat()
            if presence == "present"
            else None,
            "last_seen_at": observed_at.isoformat() if presence == "present" else None,
            "stationary_since": (observed_at - timedelta(seconds=6)).isoformat()
            if presence == "present" and motion == "stationary"
            else None,
            "absent_since": (observed_at - timedelta(seconds=8)).isoformat()
            if presence == "absent"
            else None,
            "detection_count": 1 if presence == "present" else 0,
            "reason": "test_body_observation",
            "number_associated": (bool(number) if associated is None else associated)
            if presence == "present"
            else False,
        }
    pipeline.observe.return_value = transport_recognition.TransportObservation(
        number=number,
        observed_at=observed_at,
        frame_id=frame_id or f"frame-{pipeline.serial}",
        snapshot=SNAPSHOT if number else None,
        tracking=tracking,
    )
    return automation._poll_binding(binding.pk)


def _confirm(pipeline, binding, *, number=NUMBER, **tracking):
    for _ in range(3):
        _poll(pipeline, binding, number=number, **tracking)


def _finish(pipeline, binding, order, user):
    session = AiCountingSession.objects.get(order=order)
    pipeline.live[binding.conveyor_camera]["total"] = 17
    counting.stop(
        binding.conveyor_camera,
        order,
        user,
        complete_order=True,
        expected_session_id=session.pk,
    )
    order.refresh_from_db()
    session.refresh_from_db()
    assert order.status == "loaded"
    assert session.status == AiCountingSession.CLOSED
    assert order.shipment.bags_loaded == session.final_total == 17
    return session


@pytest.mark.parametrize(
    ("model", "transport", "number"),
    [("vehicle_number", "truck", NUMBER), ("wagon_number", "train", "00123456")],
)
def test_three_fresh_votes_bind_correct_transport_to_continuous_session(
    pipeline, model, transport, number
):
    binding = _binding(model=model)
    order = _order(pipeline, number=number, transport=transport)
    # The same text on the other transport type must not create ambiguity.
    wrong_type = _order(
        pipeline, number=number, transport="train" if transport == "truck" else "truck"
    )
    _poll(pipeline, binding, number=number)
    _poll(pipeline, binding, number=number)
    assert not AiCountingSession.objects.exists()
    order.refresh_from_db()
    assert order.status == "confirmed"

    _poll(pipeline, binding, number=number)

    session = AiCountingSession.objects.get()
    order.refresh_from_db()
    wrong_type.refresh_from_db()
    assert session.order_id == order.pk
    assert session.status == AiCountingSession.ACTIVE
    assert session.automatically_started and session.started_by_id is None
    assert (order.status, order.loading_camera) == ("loading", "cam2")
    assert wrong_type.status == "confirmed"
    assert order.shipment.loading_started_at is not None
    assert order.shipment.arrived_at is None
    assert order.shipment.shipped_at is None
    evidence = ShippingTransportRecognitionEvent.objects.get()
    assert (evidence.order_id, evidence.session_id, evidence.status) == (
        order.pk,
        session.pk,
        "matched",
    )
    assert evidence.number == number
    assert evidence.image.read() == SNAPSHOT
    pipeline.reset.assert_not_called()
    pipeline.delete.assert_not_called()
    pipeline.request.assert_not_called()
    pipeline.start.assert_called_once()


def test_votes_need_elapsed_confirmation_time(pipeline):
    binding = _binding()
    _order(pipeline)
    for _ in range(3):
        _poll(pipeline, binding, advance=0.4)
    assert not AiCountingSession.objects.exists()
    _poll(pipeline, binding, advance=2)
    assert AiCountingSession.objects.get().status == AiCountingSession.ACTIVE


@pytest.mark.parametrize(
    "invalid", ["duplicate", "stale", "out_of_order", "ocr_error", "no_number"]
)
def test_invalid_observation_breaks_confirmation_and_requires_three_new_votes(
    pipeline, invalid
):
    binding = _binding()
    _order(pipeline)
    _poll(pipeline, binding, frame_id="first")
    _poll(pipeline, binding, frame_id="second")
    options = {
        "duplicate": {"frame_id": "first"},
        "stale": {"age": 16},
        "out_of_order": {"age": 3},
        "ocr_error": {"error": ai.AiUnavailable("camera offline")},
        "no_number": {"number": None},
    }[invalid]
    _poll(pipeline, binding, **options)
    assert not AiCountingSession.objects.exists()
    assert not ShippingTransportRecognitionEvent.objects.exists()
    assert ShippingTransportState.objects.get().confirmations == 0
    _poll(pipeline, binding)
    _poll(pipeline, binding)
    assert not AiCountingSession.objects.exists()
    _poll(pipeline, binding)
    assert AiCountingSession.objects.get().status == AiCountingSession.ACTIVE


def test_changing_number_does_not_combine_votes_from_different_vehicles(pipeline):
    binding = _binding()
    _order(pipeline)
    for number in (NUMBER, OTHER_NUMBER, NUMBER, OTHER_NUMBER, NUMBER):
        _poll(pipeline, binding, number=number)
    assert not AiCountingSession.objects.exists()
    assert ShippingTransportState.objects.get().confirmations == 1


@pytest.mark.parametrize("matches", [0, 2])
def test_unmatched_or_ambiguous_number_is_journaled_once_without_selecting_order(
    pipeline, matches
):
    binding = _binding()
    orders = [_order(pipeline) for _ in range(matches)]
    _confirm(pipeline, binding)
    evidence = ShippingTransportRecognitionEvent.objects.get()
    expected_status = "no_order" if matches == 0 else "multiple_orders"
    assert evidence.status == expected_status
    assert evidence.number == NUMBER
    assert evidence.order_id is None and evidence.session_id is None
    assert evidence.last_seen_at - evidence.first_seen_at == timedelta(seconds=4)
    assert evidence.image.read() == SNAPSHOT
    original_first_seen = evidence.first_seen_at
    _confirm(pipeline, binding)
    evidence.refresh_from_db()
    assert ShippingTransportRecognitionEvent.objects.count() == 1
    assert evidence.first_seen_at == original_first_seen
    assert evidence.last_seen_at == pipeline.clock.now
    assert not AiCountingSession.objects.exists()
    assert all(Order.objects.get(pk=order.pk).status == "confirmed" for order in orders)
    pipeline.start.assert_not_called()


@pytest.mark.parametrize(
    "status", ["draft", "pending", "loaded", "shipped", "cancelled"]
)
def test_ineligible_order_status_does_not_acquire(pipeline, status):
    binding = _binding()
    _order(pipeline, status=status)
    _confirm(pipeline, binding)
    assert not AiCountingSession.objects.exists()
    assert ShippingTransportRecognitionEvent.objects.get().status == "no_order"


def test_archived_order_is_not_acquired(pipeline):
    binding = _binding()
    _order(pipeline, deleted_at=pipeline.clock.now)
    _confirm(pipeline, binding)
    assert not AiCountingSession.objects.exists()
    assert ShippingTransportRecognitionEvent.objects.get().status == "no_order"


def test_number_formatting_matches_without_dropping_leading_zeroes(pipeline):
    binding = _binding()
    order = _order(pipeline, number=" 001 abc-01 ")
    _confirm(pipeline, binding, number="001ABC01")
    assert AiCountingSession.objects.get().order_id == order.pk


def test_order_reserved_on_another_camera_is_never_reserved_twice(pipeline):
    binding = _binding()
    order = _order(pipeline)
    original = AiCountingSession.objects.create(
        order=order,
        camera="cam3",
        status=AiCountingSession.STARTING,
        automatically_started=True,
    )
    _confirm(pipeline, binding)
    assert list(AiCountingSession.objects.values_list("pk", flat=True)) == [original.pk]
    assert ShippingTransportState.objects.get().state == "error"
    order.refresh_from_db()
    assert order.status == "confirmed"
    pipeline.start.assert_not_called()


def test_busy_camera_does_not_replace_existing_manual_loading(pipeline, operator):
    binding = _binding()
    owner = _order(pipeline, number=OTHER_NUMBER)
    candidate = _order(pipeline)
    original = AiCountingSession.objects.create(
        order=owner,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=operator,
    )
    _confirm(pipeline, binding)
    assert list(AiCountingSession.objects.values_list("pk", flat=True)) == [original.pk]
    assert ShippingTransportState.objects.get().state == "busy"
    candidate.refresh_from_db()
    assert candidate.status == "confirmed"
    assert pipeline.observe.call_count == 3
    pipeline.start.assert_not_called()


def test_lost_start_ack_resumes_same_reservation_and_remote_baseline(pipeline):
    binding = _binding()
    order = _order(pipeline)

    def lose_ack(camera, options):
        pipeline.start_impl(camera, options)
        pipeline.live[camera]["total"] = 9
        raise ai.AiUnavailable("acknowledgement lost")

    pipeline.start.side_effect = lose_ack
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    assert session.status == AiCountingSession.STARTING
    assert ShippingTransportState.objects.get().session_id == session.pk
    order.refresh_from_db()
    assert order.status == "confirmed"
    _confirm(pipeline, binding)
    session.refresh_from_db()
    order.refresh_from_db()
    assert AiCountingSession.objects.count() == 1
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"
    assert session.last_status["total"] == 9
    pipeline.start.assert_called_once()


def test_lost_ack_reconciles_known_session_even_if_another_matching_order_appears(
    pipeline,
):
    binding = _binding()
    first_order = _order(pipeline)

    def lose_ack(camera, options):
        pipeline.start_impl(camera, options)
        pipeline.live[camera]["total"] = 9
        raise ai.AiUnavailable("acknowledgement lost")

    pipeline.start.side_effect = lose_ack
    _confirm(pipeline, binding)
    reserved = AiCountingSession.objects.get()
    assert reserved.status == AiCountingSession.STARTING
    second_order = _order(pipeline)
    _confirm(pipeline, binding)
    reserved.refresh_from_db()
    first_order.refresh_from_db()
    second_order.refresh_from_db()
    assert reserved.status == AiCountingSession.ACTIVE
    assert first_order.status == "loading"
    assert second_order.status == "confirmed"
    assert AiCountingSession.objects.count() == 1
    assert reserved.last_status["total"] == 9
    pipeline.start.assert_called_once()


def test_lost_ack_with_occluded_plate_reconciles_exact_remote_session(pipeline):
    binding = _binding()
    order = _order(pipeline)

    def lose_ack(camera, options):
        pipeline.start_impl(camera, options)
        raise ai.AiUnavailable("acknowledgement lost")

    pipeline.start.side_effect = lose_ack
    _confirm(pipeline, binding)
    reserved = AiCountingSession.objects.get()
    assert reserved.status == AiCountingSession.STARTING
    _poll(pipeline, binding, error=ai.AiUnavailable("number camera occluded"))
    reserved.refresh_from_db()
    order.refresh_from_db()
    assert reserved.status == AiCountingSession.ACTIVE
    assert order.status == "loading"
    assert AiCountingSession.objects.count() == 1
    pipeline.start.assert_called_once()


@pytest.mark.parametrize("configuration_change", ["edit", "recreate"])
def test_completed_same_vehicle_stays_latched_across_configuration_changes(
    pipeline, operator, configuration_change
):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    session = _finish(pipeline, binding, order, operator)
    second_order = _order(pipeline)
    state_id = ShippingTransportState.objects.get().pk
    pipeline.clock.now += timedelta(seconds=2)
    if configuration_change == "recreate":
        binding.delete()
        assert ShippingTransportState.objects.get(pk=state_id).binding_id is None
        assert ShippingTransportRecognitionEvent.objects.filter(
            session=session
        ).exists()
        binding = _binding()
    else:
        binding.number_camera = "cam8"
        binding.save()
    _confirm(pipeline, binding)
    state = ShippingTransportState.objects.get()
    assert state.pk == state_id
    assert state.claimed_number == NUMBER
    assert state.session_id == session.pk
    assert state.state == "completed"
    assert AiCountingSession.objects.count() == 1
    second_order.refresh_from_db()
    assert second_order.status == "confirmed"
    pipeline.start.assert_called_once()


def test_changed_number_or_visit_without_clear_cannot_release_completed_visit_latch(
    pipeline, operator
):
    binding = _binding()
    first_order = _order(pipeline)
    _confirm(pipeline, binding)
    _finish(pipeline, binding, first_order, operator)
    second_order = _order(pipeline)
    _confirm(pipeline, binding, number=OTHER_NUMBER, visit_id="another-visit")
    state = ShippingTransportState.objects.get()
    assert state.claimed_number == NUMBER and state.session_id is not None
    assert state.claimed_visit_id == "visit-1"
    assert state.state == "completed"
    assert ShippingTransportRecognitionEvent.objects.filter(
        number=OTHER_NUMBER, status="no_order"
    ).exists()
    _confirm(pipeline, binding)
    second_order.refresh_from_db()
    assert second_order.status == "confirmed"
    assert not AiCountingSession.objects.filter(order=second_order).exists()


def test_unreadable_number_after_completion_does_not_release_visit_latch(
    pipeline, operator
):
    binding = _binding()
    first_order = _order(pipeline)
    _confirm(pipeline, binding)
    original = _finish(pipeline, binding, first_order, operator)
    waiting_order = _order(pipeline)
    _poll(pipeline, binding, number=None)
    _poll(pipeline, binding, error=ai.AiUnavailable("number camera offline"))
    _confirm(pipeline, binding)
    waiting_order.refresh_from_db()
    assert waiting_order.status == "confirmed"
    assert AiCountingSession.objects.count() == 1
    state = ShippingTransportState.objects.get()
    assert state.state == "completed"
    assert state.session_id == original.pk


def test_explicit_departure_requires_new_votes_and_allows_next_same_number_visit(
    pipeline, operator
):
    binding = _binding()
    first_order = _order(pipeline)
    _confirm(pipeline, binding)
    _finish(pipeline, binding, first_order, operator)
    second_order = _order(pipeline)
    # The separate shipping workflow is authoritative for physical exit.
    pipeline.clock.now += timedelta(seconds=2)
    Order.objects.filter(pk=first_order.pk).update(status="shipped")
    shipment = first_order.shipment
    shipment.shipped_at = pipeline.clock.now
    shipment.save(update_fields=["shipped_at"])
    _poll(pipeline, binding)
    _poll(pipeline, binding)
    assert AiCountingSession.objects.count() == 1
    _poll(pipeline, binding)
    second_order.refresh_from_db()
    assert second_order.status == "loading"
    assert AiCountingSession.objects.filter(order=second_order).count() == 1
    assert ShippingTransportRecognitionEvent.objects.count() == 2


def test_lost_number_does_not_stop_an_active_loading_or_reset_counts(pipeline):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    pipeline.live["cam2"]["total"] = 11
    pipeline.observe.reset_mock()
    _poll(pipeline, binding, error=ai.AiUnavailable("number camera offline"))
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert (order.status, order.loading_camera) == ("loading", "cam2")
    assert pipeline.live["cam2"]["total"] == 11
    pipeline.start.assert_called_once()
    pipeline.observe.assert_called_once()
    state = ShippingTransportState.objects.get()
    assert state.tracking["presence"] == "unknown"
    assert state.claimed_visit_id == "visit-1"
    assert state.departure_observed_at is None
    pipeline.delete.assert_not_called()
    pipeline.reset.assert_not_called()


def test_counter_outage_keeps_active_order_and_reports_error(pipeline):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    pipeline.status.side_effect = ai.AiUnavailable("counter offline")
    assert _poll(pipeline, binding)
    session = AiCountingSession.objects.get()
    order.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert order.status == "loading"
    assert ShippingTransportState.objects.get().state == "error"
    pipeline.delete.assert_not_called()


@pytest.mark.parametrize(
    ("model", "transport"), [("vehicle_number", "truck"), ("wagon_number", "train")]
)
def test_automatic_session_control_requires_transport_loading_permission(
    pipeline, model, transport, user_with_perms, client_user
):
    binding = _binding(model=model)
    number = NUMBER if transport == "truck" else "00123456"
    order = _order(pipeline, number=number, transport=transport)
    _confirm(pipeline, binding, number=number)
    session = AiCountingSession.objects.get()
    permitted = user_with_perms(
        "transport-loader",
        codes=["train.load" if transport == "train" else "shipping.load"],
    )
    wrong_transport = user_with_perms(
        "other-loader",
        codes=["shipping.load" if transport == "train" else "train.load"],
    )
    viewer = user_with_perms("transport-viewer", codes=["shipping.view", "train.view"])
    assert session_started_by_name(session) == "Автоматически"
    assert can_control_session(session, permitted)
    assert not can_control_session(session, wrong_transport)
    assert not can_control_session(session, viewer)
    assert not can_control_session(session, client_user)
    assert not can_control_session(session, None)
    with pytest.raises(PermissionDenied):
        counting.stop(
            "cam2", order, viewer, complete_order=True, expected_session_id=session.pk
        )
    pipeline.delete.assert_not_called()
    _finish(pipeline, binding, order, permitted)


@pytest.mark.parametrize(
    "tracking",
    [
        {"presence": "unknown"},
        {"presence": "absent"},
        {"motion": "moving"},
        {"motion": "unknown"},
        {"associated": False},
        {"tracking": {}},
    ],
)
def test_numbers_without_confirmed_stationary_associated_body_never_acquire(
    pipeline, tracking
):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding, **tracking)
    assert not AiCountingSession.objects.exists()
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert ShippingTransportState.objects.get().confirmations == 0
    pipeline.start.assert_not_called()


def test_ocr_votes_cannot_be_combined_across_physical_visits(pipeline):
    binding = _binding()
    _order(pipeline)
    _poll(pipeline, binding, visit_id="visit-1")
    _poll(pipeline, binding, visit_id="visit-1")
    _poll(pipeline, binding, visit_id="visit-2")
    assert not AiCountingSession.objects.exists()
    state = ShippingTransportState.objects.get()
    assert state.candidate_visit_id == "visit-2" and state.confirmations == 1
    _poll(pipeline, binding, visit_id="visit-2")
    _poll(pipeline, binding, visit_id="visit-2")
    state.refresh_from_db()
    assert state.claimed_visit_id == "visit-2"
    assert AiCountingSession.objects.get().status == AiCountingSession.ACTIVE


def test_same_unmatched_plate_on_new_visit_creates_separate_evidence(pipeline):
    binding = _binding()
    _confirm(pipeline, binding, visit_id="visit-1")
    _poll(pipeline, binding, presence="absent", number=None)
    _confirm(pipeline, binding, visit_id="visit-2")
    assert set(
        ShippingTransportRecognitionEvent.objects.values_list("visit_id", flat=True)
    ) == {"visit-1", "visit-2"}
    assert not AiCountingSession.objects.exists()


def test_confirmed_absence_during_loading_alerts_without_closing_or_resetting(pipeline):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    original_evidence = ShippingTransportRecognitionEvent.objects.get()
    last_present_at = original_evidence.last_seen_at
    pipeline.live["cam2"]["total"] = 23
    _poll(pipeline, binding, presence="absent", number=None)
    state = ShippingTransportState.objects.get()
    order.refresh_from_db()
    session.refresh_from_db()
    assert state.tracking["presence"] == "absent"
    assert state.departure_observed_at == pipeline.clock.now
    assert state.tracking_alert
    assert state.session_id == session.pk
    assert state.claimed_number == NUMBER and state.claimed_visit_id == "visit-1"
    assert (order.status, session.status) == ("loading", AiCountingSession.ACTIVE)
    assert pipeline.live["cam2"]["total"] == 23
    departure = ShippingTransportRecognitionEvent.objects.get(status="tracking_alert")
    assert departure.session_id == session.pk and departure.order_id == order.pk
    assert departure.tracking["presence"] == "absent"
    assert departure.last_seen_at == last_present_at
    original_evidence.refresh_from_db()
    assert original_evidence.tracking["presence"] == "present"
    assert original_evidence.last_seen_at == last_present_at
    # Repeated snapshots of the same confirmed clear are one durable event.
    same_absence = {
        **pipeline.observe.return_value.tracking,
        "observed_at": (pipeline.clock.now + timedelta(seconds=2)).isoformat(),
    }
    _poll(pipeline, binding, number=None, tracking=same_absence)
    assert (
        ShippingTransportRecognitionEvent.objects.filter(
            status="tracking_alert"
        ).count()
        == 1
    )
    pipeline.delete.assert_not_called()
    pipeline.reset.assert_not_called()
    # A later good frame or temporary outage cannot silently dismiss the alert.
    previous_alert = state.tracking_alert
    _poll(pipeline, binding)
    _poll(pipeline, binding, error=ai.AiUnavailable("capture failed"))
    state.refresh_from_db()
    assert state.tracking_alert == previous_alert
    departure.refresh_from_db()
    assert departure.tracking["presence"] == "absent"


@pytest.mark.parametrize("change", ["visit", "number"])
def test_changed_transport_during_loading_is_evidence_for_current_order_not_reassignment(
    pipeline, change
):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    other_order = _order(pipeline, number=OTHER_NUMBER)
    visit = "visit-2" if change == "visit" else "visit-1"
    _confirm(pipeline, binding, number=OTHER_NUMBER, visit_id=visit)
    state = ShippingTransportState.objects.get()
    other_order.refresh_from_db()
    assert state.tracking_alert
    assert state.claimed_visit_id == "visit-1" and state.claimed_number == NUMBER
    assert state.session_id == session.pk
    assert other_order.status == "confirmed"
    assert AiCountingSession.objects.count() == 1
    evidence = ShippingTransportRecognitionEvent.objects.get(number=OTHER_NUMBER)
    assert (evidence.order_id, evidence.session_id, evidence.visit_id) == (
        order.pk,
        session.pk,
        visit,
    )
    assert evidence.image.read() == SNAPSHOT
    pipeline.start.assert_called_once()
    pipeline.delete.assert_not_called()


def test_body_presence_extends_same_visit_evidence_when_number_is_occluded(pipeline):
    binding = _binding()
    _order(pipeline)
    _confirm(pipeline, binding)
    evidence = ShippingTransportRecognitionEvent.objects.get()
    first_seen = evidence.first_seen_at
    _poll(pipeline, binding, number=None)
    evidence.refresh_from_db()
    assert evidence.last_seen_at == pipeline.clock.now
    assert evidence.first_seen_at == first_seen
    assert evidence.number == NUMBER
    assert evidence.tracking["presence"] == "present"
    state = ShippingTransportState.objects.get()
    assert state.departure_observed_at is None
    assert state.claimed_visit_id == "visit-1"
    assert state.tracking_alert == ""


def test_absence_is_recorded_even_when_counting_service_is_unavailable(pipeline):
    binding = _binding()
    _order(pipeline)
    _confirm(pipeline, binding)
    pipeline.status.side_effect = ai.AiUnavailable("counter unavailable")
    assert _poll(pipeline, binding, presence="absent", number=None)
    state = ShippingTransportState.objects.get()
    assert state.tracking["presence"] == "absent"
    assert state.tracking_alert
    assert state.departure_observed_at == pipeline.clock.now
    assert AiCountingSession.objects.get().status == AiCountingSession.ACTIVE


def test_only_absence_observed_after_closure_releases_completed_latch(
    pipeline, operator
):
    binding = _binding()
    first_order = _order(pipeline)
    _confirm(pipeline, binding)
    _poll(pipeline, binding, presence="absent", number=None)
    pipeline.clock.now += timedelta(seconds=1)
    _finish(pipeline, binding, first_order, operator)
    second_order = _order(pipeline, number=OTHER_NUMBER)
    _confirm(pipeline, binding, number=OTHER_NUMBER, visit_id="visit-2")
    second_order.refresh_from_db()
    assert second_order.status == "confirmed"
    assert ShippingTransportState.objects.get().state == "completed"
    _poll(pipeline, binding, presence="absent", number=None)
    _confirm(pipeline, binding, number=OTHER_NUMBER, visit_id="visit-3")
    second_order.refresh_from_db()
    assert second_order.status == "loading"
    state = ShippingTransportState.objects.get()
    assert state.claimed_visit_id == "visit-3"
    assert state.departure_observed_at is None
    assert state.tracking_alert == ""


def test_starting_reservation_without_remote_session_cannot_start_when_body_unknown(
    pipeline,
):
    binding = _binding()
    order = _order(pipeline)
    pipeline.start.side_effect = ai.AiUnavailable("request never reached camera")
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    assert session.status == AiCountingSession.STARTING
    _confirm(pipeline, binding, presence="unknown")
    session.refresh_from_db()
    order.refresh_from_db()
    assert session.status == AiCountingSession.STARTING
    assert order.status == "confirmed"
    pipeline.start.assert_called_once()


def test_body_change_before_reservation_is_rechecked_under_order_lock(
    pipeline, monkeypatch
):
    binding = _binding()
    order = _order(pipeline)
    original_start = counting.start

    def presence_changed(camera, candidate, user, **options):
        state = ShippingTransportState.objects.get()
        state.tracking = {}
        state.save(update_fields=["tracking"])
        return original_start(camera, candidate, user, **options)

    monkeypatch.setattr(counting, "start", presence_changed)
    _confirm(pipeline, binding)
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not AiCountingSession.objects.exists()
    pipeline.start.assert_not_called()


def test_slow_cleanup_expires_body_before_remote_start_and_keeps_reservation(
    pipeline, monkeypatch
):
    binding = _binding()
    order = _order(pipeline)
    cleanup = counting._finish_pending_cleanup
    delayed = False

    def slow_cleanup(*args, **kwargs):
        nonlocal delayed
        cleanup(*args, **kwargs)
        if not delayed:
            pipeline.clock.now += timedelta(seconds=16)
            delayed = True

    monkeypatch.setattr(counting, "_finish_pending_cleanup", slow_cleanup)
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    state = ShippingTransportState.objects.get()
    assert session.status == AiCountingSession.STARTING
    assert state.session_id == session.pk
    assert state.confirmations == 0
    order.refresh_from_db()
    assert order.status == "confirmed"
    pipeline.start.assert_not_called()
    _poll(pipeline, binding)
    _poll(pipeline, binding)
    pipeline.start.assert_not_called()
    _poll(pipeline, binding)
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert AiCountingSession.objects.count() == 1
    pipeline.start.assert_called_once()
    pipeline.reset.assert_not_called()
    pipeline.delete.assert_not_called()


def test_slow_missing_starting_status_requires_three_more_fresh_frames(pipeline):
    binding = _binding()
    order = _order(pipeline)
    pipeline.start.side_effect = ai.AiUnavailable("request never reached camera")
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    assert session.status == AiCountingSession.STARTING
    pipeline.start.reset_mock()
    pipeline.start.side_effect = pipeline.start_impl
    statuses = 0

    def slow_status(camera):
        nonlocal statuses
        statuses += 1
        # Three normal per-poll reconciliation reads precede counting.start's
        # final status read for the existing, still-missing reservation.
        if statuses == 4:
            pipeline.clock.now += timedelta(seconds=16)
        return pipeline.live.get(camera)

    pipeline.status.side_effect = slow_status
    _confirm(pipeline, binding)
    assert statuses == 4
    session.refresh_from_db()
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert session.status == AiCountingSession.STARTING
    assert ShippingTransportState.objects.get().confirmations == 0
    pipeline.start.assert_not_called()
    _poll(pipeline, binding)
    _poll(pipeline, binding)
    pipeline.start.assert_not_called()
    _poll(pipeline, binding)
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert AiCountingSession.objects.count() == 1
    pipeline.start.assert_called_once()
    pipeline.reset.assert_not_called()
    pipeline.delete.assert_not_called()


@pytest.mark.parametrize("recovery", ["active_missing", "starting_acknowledged"])
def test_remote_freshness_guard_does_not_block_existing_loading_recovery(
    pipeline, recovery
):
    binding = _binding()
    order = _order(pipeline)
    if recovery == "starting_acknowledged":

        def lose_ack(camera, options):
            pipeline.start_impl(camera, options)
            raise ai.AiUnavailable("acknowledgement lost")

        pipeline.start.side_effect = lose_ack
    _confirm(pipeline, binding)
    session = AiCountingSession.objects.get()
    if recovery == "active_missing":
        del pipeline.live[binding.conveyor_camera]
    pipeline.clock.now += timedelta(seconds=30)
    guard = Mock(side_effect=AssertionError("A bound loading must not require new OCR"))
    counting.start(
        binding.conveyor_camera,
        order,
        None,
        automatic=True,
        expected_session_id=session.pk,
        before_remote_start=guard,
    )
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE
    assert AiCountingSession.objects.count() == 1
    guard.assert_not_called()
    assert pipeline.start.call_count == (2 if recovery == "active_missing" else 1)


def test_raw_number_with_unknown_body_is_grouped_as_evidence_without_order_selection(
    pipeline,
):
    binding = _binding()
    _order(pipeline)
    _confirm(pipeline, binding, presence="unknown", visit_id=None)
    evidence = ShippingTransportRecognitionEvent.objects.get()
    assert evidence.status == "observed"
    assert evidence.number == NUMBER and evidence.visit_id == ""
    assert evidence.image.read() == SNAPSHOT
    assert evidence.order_id is None and evidence.session_id is None
    assert evidence.last_seen_at - evidence.first_seen_at == timedelta(seconds=4)
    assert evidence.tracking["presence"] == "unknown"
    assert not AiCountingSession.objects.exists()
    pipeline.start.assert_not_called()

    _poll(pipeline, binding, presence="unknown", visit_id=None, advance=16)
    assert ShippingTransportRecognitionEvent.objects.count() == 2
    _poll(pipeline, binding, presence="unknown", visit_id=None)
    assert ShippingTransportRecognitionEvent.objects.count() == 2


@pytest.mark.parametrize(
    "tracking", [{"motion": "moving"}, {"associated": False}, {"presence": "absent"}]
)
def test_unassociated_raw_number_is_retained_without_acquiring_order(
    pipeline, tracking
):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding, **tracking)
    evidence = ShippingTransportRecognitionEvent.objects.get()
    assert evidence.status == "observed" and evidence.number == NUMBER
    assert evidence.order_id is None
    order.refresh_from_db()
    assert order.status == "confirmed"
    pipeline.start.assert_not_called()


def test_raw_observed_evidence_is_separate_from_later_confirmed_match(pipeline):
    binding = _binding()
    order = _order(pipeline)
    _poll(pipeline, binding, associated=False)
    observed = ShippingTransportRecognitionEvent.objects.get()
    _confirm(pipeline, binding)
    observed.refresh_from_db()
    assert observed.status == "observed"
    assert observed.order_id is None
    assert not observed.tracking["number_associated"]
    matched = ShippingTransportRecognitionEvent.objects.get(status="matched")
    assert matched.order_id == order.pk
    assert matched.pk != observed.pk


def test_raw_number_during_active_loading_belongs_to_order_without_changing_match(
    pipeline,
):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    matched = ShippingTransportRecognitionEvent.objects.get()
    _poll(pipeline, binding, number=OTHER_NUMBER, presence="unknown", visit_id=None)
    observed = ShippingTransportRecognitionEvent.objects.get(status="observed")
    assert observed.order_id == order.pk and observed.session_id == matched.session_id
    assert observed.number == OTHER_NUMBER and observed.image.read() == SNAPSHOT
    matched.refresh_from_db()
    assert matched.status == "matched" and matched.number == NUMBER
    assert matched.tracking["presence"] == "present"
    assert AiCountingSession.objects.get().status == AiCountingSession.ACTIVE
    pipeline.start.assert_called_once()
    pipeline.delete.assert_not_called()


def test_active_absence_with_raw_number_creates_one_alert_not_duplicate_observation(
    pipeline,
):
    binding = _binding()
    order = _order(pipeline)
    _confirm(pipeline, binding)
    _poll(pipeline, binding, presence="absent")
    alert = ShippingTransportRecognitionEvent.objects.get(status="tracking_alert")
    assert alert.order_id == order.pk
    assert alert.tracking["presence"] == "absent"
    assert alert.image.read() == SNAPSHOT
    assert ShippingTransportRecognitionEvent.objects.count() == 2
    assert not ShippingTransportRecognitionEvent.objects.filter(
        status="observed"
    ).exists()


@pytest.mark.parametrize("invalid", ["stale", "duplicate", "error"])
def test_rejected_frame_does_not_create_raw_observed_evidence(pipeline, invalid):
    binding = _binding()
    _poll(pipeline, binding, number=None, frame_id="seen-frame")
    options = {
        "stale": {"age": 20},
        "duplicate": {"frame_id": "seen-frame"},
        "error": {"error": ai.AiUnavailable("OCR unavailable")},
    }
    _poll(pipeline, binding, presence="unknown", visit_id=None, **options[invalid])
    assert not ShippingTransportRecognitionEvent.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_slow_counter_lane_does_not_prevent_other_lane_acquiring_three_fresh_frames(
    pipeline, monkeypatch
):
    worker = automation._worker

    def isolated_worker(binding_id):
        try:
            return worker(binding_id)
        finally:
            # Production keeps per-thread connections for its persistent pool.
            # Test threads must release them before Django drops the test DB.
            connections.close_all()

    monkeypatch.setattr(automation, "_worker", isolated_worker)
    slow_binding = _binding(camera="cam2")
    slow_order = _order(pipeline)
    _confirm(pipeline, slow_binding)
    slow_session = AiCountingSession.objects.get(order=slow_order)
    fast_binding = _binding(camera="cam3")
    fast_order = _order(pipeline, number=OTHER_NUMBER)
    blocked = Event()
    release = Event()
    monotonic = SimpleNamespace(now=0.0)
    monkeypatch.setattr(scheduling.time, "monotonic", lambda: monotonic.now)

    def status(camera):
        if camera == slow_binding.conveyor_camera:
            blocked.set()
            assert release.wait(10), "test did not release the blocked counter"
        return pipeline.live.get(camera)

    def observe(camera, recognition_model):
        binding = slow_binding if camera == slow_binding.number_camera else fast_binding
        observed_at = pipeline.clock.now
        return transport_recognition.TransportObservation(
            number=NUMBER if binding.pk == slow_binding.pk else OTHER_NUMBER,
            frame_id=f"{binding.pk}-{observed_at.isoformat()}",
            observed_at=observed_at,
            snapshot=SNAPSHOT,
            tracking={
                "schema_version": 1,
                "basis": "transport_body",
                "presence": "present",
                "motion": "stationary",
                "visit_id": "visit-1" if binding.pk == slow_binding.pk else "visit-2",
                "observed_at": observed_at.isoformat(),
                "present_since": (observed_at - timedelta(seconds=10)).isoformat(),
                "last_seen_at": observed_at.isoformat(),
                "stationary_since": (observed_at - timedelta(seconds=6)).isoformat(),
                "absent_since": None,
                "detection_count": 1,
                "reason": "test_body_observation",
                "number_associated": True,
            },
        )

    pipeline.status.side_effect = status
    pipeline.observe.side_effect = observe
    scheduler = scheduling.ShippingTransportScheduler(2, max_workers=2)
    try:
        for iteration in range(3):
            monotonic.now = iteration * 2
            pipeline.clock.now += timedelta(seconds=2)
            scheduler.tick()
            assert scheduler._in_flight[fast_binding.pk].result(timeout=5) is False
            assert blocked.wait(5)
            assert not scheduler._in_flight[slow_binding.pk].done()
        fast_order.refresh_from_db()
        slow_session.refresh_from_db()
        assert fast_order.status == "loading"
        assert (
            AiCountingSession.objects.get(order=fast_order).status
            == AiCountingSession.ACTIVE
        )
        assert slow_session.status == AiCountingSession.ACTIVE
        assert AiCountingSession.objects.count() == 2
        pipeline.reset.assert_not_called()
        pipeline.delete.assert_not_called()
        pipeline.request.assert_not_called()
    finally:
        release.set()
        scheduler.close()
