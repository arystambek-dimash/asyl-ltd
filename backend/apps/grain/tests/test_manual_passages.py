from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from importlib import import_module
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone

from apps.eventlog.models import EventLog
from rest_framework.exceptions import ValidationError

from apps.grain import automatic_routing, manual_passages, services, statuses as st
from apps.grain.models import (
    AutomaticPassageCapture, GrainMovement, PassageScaleAutomationState,
    UnassignedWeighing, VehicleTareMemory, Wagon, WeighingIdentityCheck,
    WeighingRecord,
)
from apps.sys_permissions.models import Permission

pytestmark = pytest.mark.django_db
ENTRY_URL = "/api/grain/passages/manual-entry/"


@pytest.fixture
def editor(user_with_perms, settings):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    return user_with_perms("weighing-editor", codes=["grain.correct_weighing", "grain.view"])


@pytest.fixture
def payload():
    return {
        "number": "904 WLY 13", "cargo_name": "Отруби",
        "entry_weight_kg": 3980,
        "arrived_at": (timezone.now()-timedelta(days=1)).isoformat(),
        "reason": "Заезд восстановлен по журналу весов",
    }


def _item(**kwargs):
    return UnassignedWeighing.objects.create(**{
        "weight_kg": 8640, "stable_weight_at": timezone.now()-timedelta(minutes=2),
        "orientation": "rear", "vehicle_number": "904WLY13",
        "photo": "grain/exit.jpg", "photo_request_id": uuid4(), "camera": "cam1",
        **kwargs,
    })


def _completed():
    arrival, departure = timezone.now()-timedelta(days=1), timezone.now()-timedelta(hours=1)
    wagon = Wagon.objects.create(
        direction=Wagon.PASSAGE, workflow="simple", number="904WLY13",
        status=st.COMPLETED, gross_weight_kg=3980, tare_weight_kg=8640,
        net_weight_kg=4660, arrived_at=arrival, exited_at=departure,
        silo_arrived_at=arrival, unloading_started_at=arrival,
        unloading_finished_at=departure,
    )
    original = WeighingRecord.objects.create(
        wagon=wagon, kind="tare", source="scale", weight_kg=8640,
        photo="grain/exit.jpg", photo_request_id=uuid4(), orientation="rear",
    )
    WeighingRecord.objects.filter(pk=original.pk).update(created_at=departure)
    original.refresh_from_db()
    return wagon, original


def _correct(client, wagon, **kwargs):
    return client.post(f"/api/grain/passages/{wagon.pk}/correct-exit-weight/", {
        "exit_weight_kg": 8700, "expected_exit_weight_kg": 8640,
        "reason": "Исправлено по первичной записи весов", **kwargs,
    }, format="json")


def test_manual_entry_without_photo_is_audited_and_never_calls_hardware(auth_client, editor, payload):
    with patch("apps.grain.scale.read_truck_scale", side_effect=AssertionError("No scale")), patch(
        "apps.grain.services._read_and_store_scale_weight", side_effect=AssertionError("No camera")
    ):
        response = auth_client(editor).post(ENTRY_URL, payload, format="json")
    assert response.status_code == 201, response.data
    wagon = Wagon.objects.get(pk=response.data["id"])
    assert wagon.number == "904WLY13" and wagon.status == st.AT_SILO
    assert wagon.gross_weight_kg == 3980 and wagon.tare_weight_kg is None
    assert wagon.arrived_by == editor and wagon.number_source == "manual"
    record = wagon.weighings.get()
    assert record.source == "manual" and record.operator == editor
    assert record.manual_reason == payload["reason"] and record.created_at == wagon.arrived_at
    assert not record.photo and record.photo_request_id is None and record.orientation == ""
    assert response.data["entry_photo_url"] is None
    event = EventLog.objects.get(event_type="grain_manual_entry")
    assert event.user == editor and event.payload["before"] is None
    assert event.payload["after"]["entry_weight_kg"] == 3980
    assert event.payload["reason"] == payload["reason"]
    assert VehicleTareMemory.objects.get(number="904WLY13").record_id == record.pk
    assert record.source == "manual"  # Saved tare retains the explicitly entered provenance.
    assert not GrainMovement.objects.exists()


def test_manual_entry_can_close_later_automatically_by_exact_plate(auth_client, editor, payload):
    response = auth_client(editor).post(ENTRY_URL, payload, format="json")
    wagon = Wagon.objects.get(pk=response.data["id"])
    item = _item()
    booked = automatic_routing.book(item, "904WLY13", "rear")
    wagon.refresh_from_db()
    assert booked.wagon_id == wagon.pk and booked.action == "exit"
    assert wagon.status == st.COMPLETED and wagon.net_weight_kg == 4660
    assert wagon.exited_at == item.stable_weight_at
    assert wagon.weighings.get(kind="tare").photo == item.photo


def test_recovery_atomically_links_saved_exit_with_original_evidence(auth_client, editor, payload):
    capture = AutomaticPassageCapture.objects.create(idempotency_key=uuid4(), camera="cam1", status="completed")
    item = _item(capture=capture)
    check = WeighingIdentityCheck.objects.create(weighing=item, status="processing", lease_until=timezone.now()+timedelta(minutes=1), evidence={"original_number": "904WLY13"})
    response = auth_client(editor).post(ENTRY_URL, {**payload, "unassigned_weighing": item.pk}, format="json")
    assert response.status_code == 201, response.data
    item.refresh_from_db(); capture.refresh_from_db(); check.refresh_from_db()
    wagon = item.wagon
    assert wagon.status == st.COMPLETED and wagon.net_weight_kg == 4660
    assert item.status == "assigned" and item.action == "exit" and item.resolved_by == editor
    assert capture.wagon_id == wagon.pk and capture.action == "exit"
    assert check.status == "matched" and check.lease_until is None
    assert check.evidence == {"original_number": "904WLY13"}
    original_exit = wagon.weighings.get(kind="tare")
    assert original_exit.weight_kg == item.weight_kg == 8640
    assert original_exit.photo == item.photo and original_exit.photo_request_id == item.photo_request_id
    assert original_exit.source == "scale" and original_exit.created_at == item.stable_weight_at == wagon.exited_at
    retry = auth_client(editor).post(ENTRY_URL, {**payload, "unassigned_weighing": item.pk}, format="json")
    assert retry.status_code == 400 and Wagon.objects.count() == 1 and WeighingRecord.objects.count() == 2


@pytest.mark.parametrize("updates", [
    {"orientation": "front"}, {"vehicle_number": "123ABC13"},
    {"status": "assigned"}, {"status": "discarded"}, {"weight_kg": 3980},
    {"stable_weight_at": timezone.now()-timedelta(days=2)},
])
def test_saved_exit_recovery_rejects_conflicts_without_partial_entry(auth_client, editor, payload, updates):
    item = _item(**updates)
    response = auth_client(editor).post(ENTRY_URL, {**payload, "unassigned_weighing": item.pk}, format="json")
    assert response.status_code == 400, response.data
    assert not Wagon.objects.exists() and not WeighingRecord.objects.exists()
    assert not EventLog.objects.exists()


def test_open_visit_and_duplicate_arrival_are_rejected(auth_client, editor, payload):
    client = auth_client(editor)
    assert client.post(ENTRY_URL, payload, format="json").status_code == 201
    assert client.post(ENTRY_URL, payload, format="json").data["code"] == "passage_already_on_site"
    automatic_routing.book(_item(), "904WLY13", "rear")
    assert client.post(ENTRY_URL, payload, format="json").data["code"] == "manual_entry_already_recorded"
    assert Wagon.objects.count() == 1


def test_exit_correction_appends_record_and_preserves_scale_evidence_and_times(auth_client, editor):
    wagon, original = _completed()
    before = {key: getattr(wagon, key) for key in ["arrived_at", "exited_at", "silo_arrived_at", "unloading_started_at", "unloading_finished_at"]}
    original_fields = {key: getattr(original, key) for key in ["weight_kg", "photo", "photo_request_id", "created_at", "kind", "source"]}
    response = _correct(auth_client(editor), wagon)
    assert response.status_code == 200, response.data
    wagon.refresh_from_db(); original.refresh_from_db()
    assert wagon.tare_weight_kg == 8700 and wagon.net_weight_kg == 4720 and wagon.status == st.COMPLETED
    assert all(getattr(wagon, key) == value for key, value in before.items())
    assert all(getattr(original, key) == value for key, value in original_fields.items())
    correction = wagon.weighings.exclude(pk=original.pk).get()
    assert correction.source == "manual" and correction.previous_weight_kg == 8640
    assert correction.operator == editor and correction.weight_kg == 8700
    assert not correction.photo and correction.photo_request_id is None
    assert f"/weighing/{original.pk}/" in response.data["exit_photo_url"]
    event = EventLog.objects.get(event_type="grain_exit_weight_corrected")
    assert event.user == editor
    assert event.payload["before"]["exit_weight_kg"] == 8640
    assert event.payload["after"]["exit_weight_kg"] == 8700
    assert event.payload["before"]["net_weight_kg"] == 4660
    assert event.payload["after"]["net_weight_kg"] == 4720
    assert not GrainMovement.objects.exists()
    # An old browser tab or a request retry must not silently override this correction.
    assert _correct(auth_client(editor), wagon).data["code"] == "exit_weight_conflict"
    assert wagon.weighings.count() == 2


def test_missing_exit_can_be_completed_with_explicit_nullable_guard(auth_client, editor, payload):
    client = auth_client(editor)
    wagon = Wagon.objects.get(pk=client.post(ENTRY_URL, payload, format="json").data["id"])
    response = _correct(client, wagon, expected_exit_weight_kg=None)
    assert response.status_code == 200, response.data
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED and wagon.tare_weight_kg == 8700
    assert wagon.exited_at is not None
    assert wagon.weighings.get(kind="tare").previous_weight_kg is None
    assert _correct(client, wagon, expected_exit_weight_kg=None).status_code == 400


@pytest.mark.parametrize("value", [0, -1, True, 12.5, 3980.0, "3980.0", "1e4", None, "", [], {}, 2**63])
def test_weights_reject_non_positive_non_integer_or_overflow(auth_client, editor, payload, value):
    client = auth_client(editor)
    response = client.post(ENTRY_URL, {**payload, "entry_weight_kg": value}, format="json")
    assert response.status_code == 400
    assert not Wagon.objects.exists()
    wagon, original = _completed()
    assert _correct(client, wagon, exit_weight_kg=value).status_code == 400
    assert wagon.weighings.count() == 1


@pytest.mark.parametrize("changes", [
    {"reason": ""}, {"reason": "     "}, {"reason": "a"*301},
    {"number": ""}, {"number": "UNKNOWN"}, {"cargo_name": ""},
    {"arrived_at": (timezone.now()+timedelta(days=1)).isoformat()},
    {"arrived_at": "not a time"},
])
def test_manual_entry_requires_identity_time_and_reason(auth_client, editor, payload, changes):
    assert auth_client(editor).post(ENTRY_URL, {**payload, **changes}, format="json").status_code == 400
    assert not Wagon.objects.exists()


def test_correction_rejects_missing_expected_guard_and_invalid_net(auth_client, editor):
    wagon, original = _completed()
    client = auth_client(editor)
    url = f"/api/grain/passages/{wagon.pk}/correct-exit-weight/"
    assert client.post(url, {"exit_weight_kg": 9000, "reason": "Причина изменения"}, format="json").status_code == 400
    for weight in [3980, 3900, 8640]:
        assert _correct(client, wagon, exit_weight_kg=weight).status_code == 400
    assert wagon.weighings.count() == 1


@pytest.mark.parametrize("codes", [[], ["grain.view"], ["grain.weigh", "grain.arrive"], ["grain.admin"]])
def test_manual_commands_require_dedicated_permission(auth_client, user_with_perms, payload, codes):
    user = user_with_perms("limited-editor", codes=codes)
    client = auth_client(user)
    wagon, _ = _completed()
    assert client.post(ENTRY_URL, payload, format="json").status_code == 403
    assert _correct(client, wagon).status_code == 403


def test_superuser_can_correct_without_employee_grant(auth_client, make_user):
    user = make_user("weighing-superuser")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    wagon, _ = _completed()
    assert _correct(auth_client(user), wagon).status_code == 200


def test_client_cannot_correct_even_with_permission(auth_client, editor, payload):
    editor.is_client = True
    editor.save(update_fields=["is_client"])
    assert auth_client(editor).post(ENTRY_URL, payload, format="json").status_code == 403


def test_intake_cannot_be_corrected_through_passage_endpoint(auth_client, editor):
    wagon, _ = _completed()
    wagon.direction = Wagon.INTAKE
    wagon.save(update_fields=["direction"])
    assert _correct(auth_client(editor), wagon).status_code == 404
    wagon.refresh_from_db()
    assert wagon.tare_weight_kg == 8640


def test_active_automatic_capture_blocks_manual_mutation_atomically(auth_client, editor, payload, settings):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    capture = AutomaticPassageCapture.objects.create(idempotency_key=uuid4(), camera="cam1", status="processing")
    state, _ = PassageScaleAutomationState.objects.update_or_create(
        scale_number="truck", defaults={"phase": "processing", "current_capture": capture},
    )
    client = auth_client(editor)
    assert client.post(ENTRY_URL, payload, format="json").data["code"] == "passage_capture_in_progress"
    assert not Wagon.objects.exists()
    wagon, _ = _completed()
    assert _correct(client, wagon).data["code"] == "passage_capture_in_progress"
    assert wagon.weighings.count() == 1
    state.refresh_from_db()
    assert state.current_capture == capture and state.phase == "processing"


def test_permission_migration_seeds_explicit_assignable_permission_idempotently():
    migration = import_module("apps.sys_permissions.migrations.0022_grain_correct_weighing_permission")
    with connection.schema_editor() as editor:
        migration.ensure_permission(apps, editor)
        migration.ensure_permission(apps, editor)
    assert Permission.objects.filter(code="grain.correct_weighing").count() == 1


@pytest.mark.django_db(transaction=True)
def test_simultaneous_recovery_creates_only_one_trip_and_exit(editor, payload):
    item = _item()
    ready = Barrier(2)

    def recover():
        close_old_connections()
        try:
            ready.wait(timeout=5)
            try:
                wagon = manual_passages.create_entry(
                    editor, **{**payload, "arrived_at": datetime.fromisoformat(payload["arrived_at"])},
                    unassigned_weighing=item.pk,
                )
                return wagon.pk
            except ValidationError:
                return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recover(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert Wagon.objects.count() == 1 and WeighingRecord.objects.count() == 2
    item.refresh_from_db()
    assert item.status == "assigned" and item.wagon.status == st.COMPLETED


@pytest.mark.django_db(transaction=True)
def test_concurrent_weight_corrections_have_one_winner(editor):
    wagon, _ = _completed()
    ready = Barrier(2)

    def correct(weight):
        close_old_connections()
        try:
            stale_wagon = Wagon.objects.get(pk=wagon.pk)
            ready.wait(timeout=5)
            try:
                manual_passages.correct_exit(
                    editor, stale_wagon, exit_weight_kg=weight,
                    expected_exit_weight_kg=8640, reason="Проверено по первичному журналу",
                )
                return weight
            except ValidationError:
                return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, [8700, 8800]))
    assert sum(result is not None for result in results) == 1
    wagon.refresh_from_db()
    assert wagon.tare_weight_kg == next(result for result in results if result is not None)
    assert wagon.weighings.count() == 2
    assert EventLog.objects.filter(event_type="grain_exit_weight_corrected").count() == 1


@pytest.mark.django_db(transaction=True)
def test_legacy_entry_and_manual_exit_recovery_share_lane_first_lock_order(editor, payload):
    # Unknown orientation is intentionally eligible for both explicit commands.
    # Hold the lane in recovery while legacy creation reaches its lane SELECT.
    # If legacy locks the event first, these two writers deadlock on PostgreSQL.
    item = _item(orientation="")
    PassageScaleAutomationState.objects.get_or_create(scale_number="truck")
    lane_held, legacy_waiting_for_lane = Event(), Event()

    def recover():
        close_old_connections()
        try:
            with transaction.atomic():
                manual_passages._lock_lane()
                lane_held.set()
                assert legacy_waiting_for_lane.wait(timeout=5)
                return manual_passages.create_entry(
                    editor, **{**payload, "arrived_at": datetime.fromisoformat(payload["arrived_at"])},
                    unassigned_weighing=item.pk,
                ).pk
        finally:
            connections.close_all()

    def legacy_entry():
        close_old_connections()
        try:
            assert lane_held.wait(timeout=5)

            def observe_lane_wait(execute, sql, params, many, context):
                if "FOR UPDATE" in sql and '"grain_passagescaleautomationstate"' in sql:
                    legacy_waiting_for_lane.set()
                return execute(sql, params, many, context)

            with connection.execute_wrapper(observe_lane_wait):
                try:
                    services.create_passage_from_unassigned_weighing(
                        item, editor, number="904WLY13", cargo_name="Отруби",
                    )
                    return "created"
                except ValidationError as exc:
                    return str(exc.detail["code"])
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovered = pool.submit(recover)
        legacy = pool.submit(legacy_entry)
        wagon_id = recovered.result(timeout=15)
        assert legacy.result(timeout=15) == "unassigned_weighing_resolved"
    assert Wagon.objects.count() == 1
    wagon = Wagon.objects.get(pk=wagon_id)
    assert wagon.status == st.COMPLETED and wagon.net_weight_kg == 4660
    assert wagon.weighings.count() == 2
    item.refresh_from_db()
    assert item.wagon_id == wagon_id and item.action == "exit"
    assert wagon.weighings.get(kind="tare").photo == item.photo


def test_legacy_rear_entry_is_rejected_before_create(editor):
    item = _item()
    with patch.object(services, "create_passage", side_effect=AssertionError("No partial entry")):
        with pytest.raises(ValidationError) as failure:
            services.create_passage_from_unassigned_weighing(item, editor)
    assert failure.value.detail["code"] == "rear_cannot_be_entry"
    assert not Wagon.objects.exists()
