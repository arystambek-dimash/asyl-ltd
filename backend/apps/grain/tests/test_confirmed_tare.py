from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from threading import Event

import pytest
from django.apps import apps
from django.db import IntegrityError, close_old_connections, connection, connections
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.models import EventLog
from apps.grain import automatic_routing, historical_tare, services, statuses as st
from apps.grain.models import PassageScaleAutomationState, UnassignedWeighing, VehicleTareMemory, Wagon, WeighingRecord
from apps.grain.serializers import WeighingRecordSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(user_with_perms):
    return user_with_perms("confirmed-tare-staff", codes=["grain.weigh"])


def _source(*, number="123SMA13", weight=4680, minutes=120, **kwargs):
    wagon = Wagon.objects.create(direction="passage", number=number, status=st.COMPLETED, cargo_name="Отруби")
    record = WeighingRecord.objects.create(
        wagon=wagon, kind="gross", weight_kg=weight, **kwargs,
    )
    WeighingRecord.objects.filter(pk=record.pk).update(created_at=timezone.now()-timedelta(minutes=minutes))
    record.refresh_from_db()
    return record


def _exit(number="123SMA13", **kwargs):
    return UnassignedWeighing.objects.create(**{
        "vehicle_number": number, "weight_kg": 9400, "orientation": "rear",
        "stable_weight_at": timezone.now()-timedelta(minutes=1), **kwargs,
    })


@pytest.mark.parametrize("number,weight", [("123SMA13", 4680), ("201DFA13", 5380)])
def test_staff_confirmed_scale_entry_without_orientation_saves_tare(staff, number, weight):
    wagon = Wagon.objects.create(direction="passage", number=number, status=st.ARRIVED, cargo_name="Отруби")
    services.record_passage_entry_weight(wagon, weight, staff, source="scale", orientation="")
    record = wagon.weighings.get(kind="gross")
    memory = VehicleTareMemory.objects.get(number=number)
    assert memory.record_id == record.pk and memory.record.weight_kg == weight
    assert record.source == "scale" and record.orientation == "" and record.operator_id == staff.pk


@pytest.mark.parametrize("source,orientation,operator,reason,eligible", [
    ("scale", "front", False, "", True),
    ("scale", "", True, "", True),
    ("scale", "", False, "", False),
    ("scale", "rear", True, "", False),
    ("manual", "", True, "Подтверждено по журналу", True),
    ("manual", "front", True, "Подтверждено по журналу", True),
    ("manual", "rear", True, "Подтверждено по журналу", False),
    ("manual", "", False, "Подтверждено по журналу", False),
    ("manual", "", True, "    ", False),
    ("manual", "", True, "\t\t\t\t\t\t", False),
    ("manual", "", True, "  abc  ", False),
    ("gpt", "front", True, "Подтверждено по журналу", False),
    ("historical", "front", True, "Подтверждено по журналу", False),
])
def test_tare_requires_confirmed_original_entry(staff, source, orientation, operator, reason, eligible):
    record = _source(source=source, orientation=orientation, operator=staff if operator else None, manual_reason=reason)
    result = historical_tare.remember(record, record.wagon.number)
    assert (result is not None) == eligible
    assert VehicleTareMemory.objects.exists() == eligible
    assert (historical_tare.latest_before(_exit(), record.wagon.number) is not None) == eligible


@pytest.mark.parametrize("source,reason", [("scale", ""), ("manual", "Подтверждено по журналу")])
def test_client_operator_is_not_staff_confirmation(staff, source, reason):
    staff.is_client = True
    staff.save(update_fields=["is_client"])
    record = _source(source=source, orientation="", operator=staff, manual_reason=reason)
    assert historical_tare.remember(record, record.wagon.number) is None


def test_historical_reference_never_becomes_latest_tare(staff):
    source = _source(source="scale", orientation="front")
    copied = _source(source="scale", orientation="front", reference_record=source)
    assert historical_tare.remember(copied, copied.wagon.number) is None


def test_manual_tare_auto_reuse_preserves_source_and_missing_photo(staff):
    source = _source(source="manual", orientation="", operator=staff, manual_reason="Заезд восстановлен по документам")
    historical_tare.remember(source, source.wagon.number)
    item = automatic_routing.book(_exit(), source.wagon.number, "rear")
    assert item.wagon.status == st.COMPLETED and item.wagon.net_weight_kg == 4720
    reference = item.wagon.weighings.get(kind="gross")
    assert reference.source == "historical" and reference.reference_record_id == source.pk
    assert reference.orientation == "" and not reference.photo
    data = WeighingRecordSerializer(reference).data
    assert data["reference_record_source"] == "manual" and data["photo_url"] is None
    assert WeighingRecordSerializer(source).data["reference_record_source"] is None
    assert VehicleTareMemory.objects.get(number=source.wagon.number).record_id == source.pk
    source.refresh_from_db()
    assert source.source == "manual" and source.orientation == ""


def test_latest_confirmed_weight_wins_even_if_older_scale_tare_is_lighter(staff):
    older = _source(source="scale", orientation="front", weight=4000, minutes=180)
    newer = _source(source="manual", operator=staff, manual_reason="Подтверждено по журналу", weight=9500, minutes=90)
    item = _exit()
    assert historical_tare.latest_before(item, item.vehicle_number).pk == newer.pk
    assert not historical_tare.candidates(item, item.vehicle_number).exists()
    for reference in [older, newer]:
        with pytest.raises(ValidationError):
            historical_tare.complete(item, staff, reference_record=reference.pk, number=item.vehicle_number, reason="Проверено по журналу")
    assert Wagon.objects.count() == 2 and item.status == "open"


def test_future_memory_does_not_replace_tare_before_historical_exit(staff):
    before = _source(source="manual", operator=staff, manual_reason="Ввод подтверждён", minutes=120)
    future = _source(source="scale", orientation="front", weight=5000, minutes=-5)
    historical_tare.remember(future, future.wagon.number)
    item = _exit()
    assert historical_tare.latest_before(item, item.vehicle_number).pk == before.pk
    assert VehicleTareMemory.objects.get(number=item.vehicle_number).record_id == future.pk


def test_setting_plate_refreshes_latest_confirmed_manual_tare(staff, settings):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    source = _source(number="", source="manual", operator=staff, manual_reason="Ввод подтверждён")
    source.wagon.status = st.AT_SILO
    source.wagon.save(update_fields=["status"])
    services.set_passage_number(source.wagon, "676 VEA 13", staff)
    assert VehicleTareMemory.objects.get(number="676VEA13").record_id == source.pk


def test_backfill_seeds_only_eligible_latest_sources_with_audit_and_preserves_newer(staff):
    migration = import_module("apps.grain.migrations.0019_confirmed_entry_tare_memory")
    measured = _source(source="scale", orientation="", operator=staff)
    manual = _source(number="201 DFA 13", source="manual", operator=staff, manual_reason="Ввод подтверждён")
    rear = _source(number="999ABC13", source="scale", orientation="rear", operator=staff)
    unknown = _source(number="998ABC13", source="scale", orientation="")
    newer = _source(number="123SMA13", source="scale", orientation="front", minutes=30)
    historical_tare.remember(newer, newer.wagon.number)
    old_memory = VehicleTareMemory.objects.get(number="123SMA13")
    with connection.schema_editor() as schema_editor:
        migration.seed_confirmed_entry_tares(apps, schema_editor)
        migration.seed_confirmed_entry_tares(apps, schema_editor)
    assert VehicleTareMemory.objects.get(number="123SMA13").record_id == old_memory.record_id
    assert VehicleTareMemory.objects.get(number="201DFA13").record_id == manual.pk
    assert not VehicleTareMemory.objects.filter(number__in=[rear.wagon.number, unknown.wagon.number]).exists()
    audit = EventLog.objects.get(event_type="grain_tare_memory_backfill")
    assert audit.payload["record_id"] == manual.pk and audit.payload["source"] == "manual"
    assert audit.payload["operator_id"] == staff.pk
    manual.refresh_from_db(); measured.refresh_from_db()
    assert manual.source == "manual" and manual.orientation == "" and not manual.photo
    assert measured.source == "scale" and measured.orientation == ""
    assert historical_tare.latest_before(_exit("201DFA13"), "201DFA13").pk == manual.pk


@pytest.mark.django_db(transaction=True)
def test_source_plate_cannot_change_between_tare_validation_and_booking(staff, settings):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    source = _source(source="manual", operator=staff, manual_reason="Ввод подтверждён")
    source.wagon.status = st.AT_SILO
    source.wagon.gross_weight_kg = source.weight_kg
    source.wagon.save(update_fields=["status", "gross_weight_kg"])
    item = _exit()
    PassageScaleAutomationState.objects.get_or_create(scale_number="truck")
    source_selected, rename_waiting_or_done = Event(), Event()

    def reuse():
        close_old_connections()
        try:
            def after_source_selected(execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if '"grain_weighingrecord"' in sql and "FOR UPDATE OF" in sql:
                    source_selected.set()
                    assert rename_waiting_or_done.wait(timeout=5)
                return result

            with connection.execute_wrapper(after_source_selected):
                try:
                    historical_tare.complete(
                        item, staff, reference_record=source.pk,
                        number="123SMA13", reason="Подтверждено по журналу", automatic=True,
                    )
                    return "booked"
                except (ValidationError, IntegrityError):
                    # The source is still on site: it cannot produce a second
                    # same-plate trip while a concurrent rename waits its turn.
                    return "rejected"
        finally:
            connections.close_all()

    def rename():
        close_old_connections()
        try:
            assert source_selected.wait(timeout=5)

            def before_lane_wait(execute, sql, params, many, context):
                if '"grain_passagescaleautomationstate"' in sql and "FOR UPDATE" in sql:
                    rename_waiting_or_done.set()
                return execute(sql, params, many, context)

            with connection.execute_wrapper(before_lane_wait):
                services.set_passage_number(source.wagon, "201DFA13", staff)
            rename_waiting_or_done.set()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reused, renamed = pool.submit(reuse), pool.submit(rename)
        assert reused.result(timeout=15) == "rejected"
        renamed.result(timeout=15)
    assert Wagon.objects.count() == 1
    source.wagon.refresh_from_db(); item.refresh_from_db()
    assert source.wagon.number == "201DFA13"
    assert item.status == "open" and item.wagon_id is None
    assert not VehicleTareMemory.objects.filter(number="123SMA13").exists()
    assert VehicleTareMemory.objects.get(number="201DFA13").record_id == source.pk
