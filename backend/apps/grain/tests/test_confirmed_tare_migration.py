from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from threading import Event

import pytest
from django.apps import apps
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from apps.eventlog.models import EventLog
from apps.grain.models import PassageScaleAutomationState, VehicleTareMemory, Wagon, WeighingRecord

pytestmark = pytest.mark.django_db


def _source(*, number="676VEA13", minutes=120, **fields):
    wagon = Wagon.objects.create(
        direction="passage", number=number, status="completed", cargo_name="Отруби",
    )
    record = WeighingRecord.objects.create(**{
        "wagon": wagon, "kind": "gross", "weight_kg": 3680,
        "source": "scale", "orientation": "front", "photo": "grain/original.jpg",
        **fields,
    })
    WeighingRecord.objects.filter(pk=record.pk).update(
        created_at=timezone.now()-timedelta(minutes=minutes),
    )
    record.refresh_from_db()
    return record


def _run():
    migration = import_module("apps.grain.migrations.0019_confirmed_entry_tare_memory")
    with connection.schema_editor() as editor:
        migration.seed_confirmed_entry_tares(apps, editor)


def _originals():
    return list(WeighingRecord.objects.order_by("pk").values())


@pytest.mark.parametrize("invalid", [
    {"kind": "tare"},
    {"orientation": "rear"},
    {"orientation": "", "operator": None},
    {"source": "manual", "manual_reason": "Причина без автора", "operator": None},
    {"source": "historical"},
])
def test_backfill_replaces_newer_invalid_pointer_with_older_confirmed_source(invalid):
    confirmed = _source()
    rejected = _source(minutes=30, weight_kg=7860, **invalid)
    VehicleTareMemory.objects.create(
        number="676VEA13", record=rejected, observed_at=rejected.created_at,
    )
    before = _originals()

    _run()
    _run()

    memory = VehicleTareMemory.objects.get(number="676VEA13")
    assert memory.record_id == confirmed.pk and memory.observed_at == confirmed.created_at
    assert _originals() == before
    event = EventLog.objects.get(event_type="grain_tare_memory_backfill")
    assert event.payload["previous_record_id"] == rejected.pk
    assert event.payload["before"] == {
        "record_id": rejected.pk, "observed_at": rejected.created_at.isoformat(),
    }
    assert event.payload["after"] == {
        "record_id": confirmed.pk, "observed_at": confirmed.created_at.isoformat(),
    }


@pytest.mark.parametrize("invalid", [
    {"kind": "tare"},
    {"orientation": "rear"},
    {"orientation": "", "operator": None},
    {"source": "historical"},
    {"source": "manual", "manual_reason": "Причина без автора", "operator": None},
])
def test_backfill_removes_invalid_memory_without_mutating_its_only_source(invalid):
    rejected = _source(**invalid)
    VehicleTareMemory.objects.create(
        number="676VEA13", record=rejected, observed_at=rejected.created_at,
    )
    before = _originals()

    _run()
    _run()

    assert not VehicleTareMemory.objects.exists()
    assert _originals() == before
    event = EventLog.objects.get(event_type="grain_tare_memory_backfill")
    assert event.payload["before"]["record_id"] == rejected.pk
    assert event.payload["record_id"] is None and event.payload["after"] is None
    assert event.payload["reason"] == "no_confirmed_entry_source"


def test_backfill_retains_newer_valid_staff_entry_without_replacing_provenance(user_with_perms):
    staff = user_with_perms("tare-migration-staff", codes=["grain.weigh"])
    _source(minutes=120)
    latest = _source(
        minutes=30, source="manual", orientation="", photo="", operator=staff,
        manual_reason="Восстановлено по первичному журналу", weight_kg=4100,
    )
    memory = VehicleTareMemory.objects.create(
        number="676VEA13", record=latest, observed_at=latest.created_at,
    )
    updated_at = memory.updated_at
    before = _originals()

    _run()
    _run()

    memory.refresh_from_db()
    assert memory.record_id == latest.pk and memory.observed_at == latest.created_at
    assert memory.updated_at == updated_at and _originals() == before
    assert not EventLog.objects.filter(event_type="grain_tare_memory_backfill").exists()


def test_backfill_uses_source_time_instead_of_a_corrupt_newer_memory_timestamp():
    older = _source(minutes=120)
    latest = _source(minutes=30, weight_kg=4100)
    VehicleTareMemory.objects.create(
        number="676VEA13", record=older, observed_at=timezone.now()+timedelta(days=1),
    )
    before = _originals()

    _run()

    memory = VehicleTareMemory.objects.get(number="676VEA13")
    assert memory.record_id == latest.pk and memory.observed_at == latest.created_at
    assert _originals() == before


def test_backfill_moves_a_renamed_source_pointer_without_rewriting_original_plate():
    renamed = _source(number="201 DFA 13")
    VehicleTareMemory.objects.create(
        number="676VEA13", record=renamed, observed_at=renamed.created_at,
    )
    before = _originals()

    _run()
    _run()

    assert not VehicleTareMemory.objects.filter(number="676VEA13").exists()
    assert VehicleTareMemory.objects.get(number="201DFA13").record_id == renamed.pk
    assert _originals() == before
    renamed.wagon.refresh_from_db()
    assert renamed.wagon.number == "201 DFA 13"
    assert EventLog.objects.filter(event_type="grain_tare_memory_backfill").count() == 2


@pytest.mark.django_db(transaction=True)
def test_backfill_waits_for_runtime_lane_and_preserves_a_newer_committed_entry():
    older = _source(minutes=120)
    VehicleTareMemory.objects.create(
        number="676VEA13", record=older, observed_at=older.created_at,
    )
    state, _ = PassageScaleAutomationState.objects.get_or_create(scale_number="truck")
    lane_requested = Event()

    def migrate():
        close_old_connections()

        def observe_lock(execute, sql, params, many, context):
            if "grain_passagescaleautomationstate" in sql and "FOR UPDATE" in sql:
                lane_requested.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(observe_lock):
                _run()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            PassageScaleAutomationState.objects.select_for_update().get(pk=state.pk)
            pending = pool.submit(migrate)
            assert lane_requested.wait(timeout=5), "Backfill did not acquire the shared runtime lane"
            # This write commits after migration started but before it acquires
            # the lane. Its source must be included in the migration snapshot.
            newer = _source(minutes=30, weight_kg=4100)
            VehicleTareMemory.objects.filter(number="676VEA13").update(
                record=newer, observed_at=newer.created_at,
            )
            before = _originals()
        pending.result(timeout=10)

    memory = VehicleTareMemory.objects.get(number="676VEA13")
    assert memory.record_id == newer.pk and memory.observed_at == newer.created_at
    assert _originals() == before
    assert not EventLog.objects.filter(event_type="grain_tare_memory_backfill").exists()
