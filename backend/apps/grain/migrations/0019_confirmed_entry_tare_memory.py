"""Backfill confirmed entries without relabelling photographs or weights."""

import re

from django.db import migrations
from django.db.models import F, Func, Q, Value
from django.db.models.functions import Length


def seed_confirmed_entry_tares(apps, schema_editor):
    Record = apps.get_model("grain", "WeighingRecord")
    Memory = apps.get_model("grain", "VehicleTareMemory")
    State = apps.get_model("grain", "PassageScaleAutomationState")
    Event = apps.get_model("eventlog", "EventLog")
    alias = schema_editor.connection.alias
    human = Q(operator__isnull=False, operator__is_client=False)
    measured = Q(source="scale") & (Q(orientation="front") | (Q(orientation="") & human))
    manual = Q(source="manual", _reason_length__gte=5, _reason_length__lte=300) & human
    rows = Record.objects.using(alias).filter(
        wagon__direction="passage", kind="gross", reference_record__isnull=True,
    ).exclude(orientation="rear").annotate(
        _reason_length=Length(Func(
            F("manual_reason"), Value(r"^\s+|\s+$"), Value(""), Value("g"),
            function="REGEXP_REPLACE",
        )),
    ).filter(measured | manual).select_related("wagon").order_by("created_at", "pk")
    if not rows.exists() and not Memory.objects.using(alias).exists():
        return  # A fresh database has no pointers to reconcile.
    # Hold the same first lock as runtime entry/rename/reuse writers before
    # reading the source snapshot. Cleanup must not race a newer confirmed
    # entry or remove its just-created memory row.
    State.objects.using(alias).select_for_update().get_or_create(scale_number="truck")
    latest = {}
    for record in rows.iterator(chunk_size=500):
        number = re.sub(r"[\s-]+", "", record.wagon.number.upper())
        if re.fullmatch(r"(?:[0-9]{3}[A-Z]{2,3}[0-9]{2}|[A-Z][0-9]{3}[A-Z]{3})", number):
            latest[number] = record
    for number, record in latest.items():
        memory, created = Memory.objects.using(alias).select_for_update().get_or_create(
            number=number, defaults={"record_id": record.pk, "observed_at": record.created_at},
        )
        previous_id = None if created else memory.record_id
        before = None if created else {
            "record_id": memory.record_id,
            "observed_at": memory.observed_at.isoformat(),
        }
        if not created:
            # Rebuild the pointer from eligible source records, not its old
            # timestamp. A former entry can have been corrected to an exit or
            # renamed; its newer memory timestamp must not protect an invalid
            # pointer. Any genuinely newer eligible entry is already in latest.
            if memory.record_id == record.pk and memory.observed_at == record.created_at:
                continue
            memory.record_id, memory.observed_at = record.pk, record.created_at
            memory.save(using=alias, update_fields=["record", "observed_at", "updated_at"])
        Event.objects.using(alias).create(
            event_type="grain_tare_memory_backfill",
            message=f"Память тары {number}: сохранён подтверждённый заезд #{record.pk}",
            payload={
                "migration": "0019_confirmed_entry_tare_memory",
                "wagon_id": record.wagon_id, "wagon_number": number,
                "previous_record_id": previous_id, "record_id": record.pk,
                "source": record.source, "weight_kg": record.weight_kg,
                "observed_at": record.created_at.isoformat(),
                "operator_id": record.operator_id,
                "before": before,
                "after": {
                    "record_id": record.pk,
                    "observed_at": record.created_at.isoformat(),
                },
            },
        )
    # A stale pointer with no eligible original entry is not a saved tare.
    # Delete only that cache row; never delete or relabel its source weighing.
    stale = Memory.objects.using(alias).select_for_update().exclude(number__in=latest)
    for memory in stale.order_by("pk").iterator(chunk_size=500):
        before = {
            "record_id": memory.record_id,
            "observed_at": memory.observed_at.isoformat(),
        }
        Event.objects.using(alias).create(
            event_type="grain_tare_memory_backfill",
            message=f"Память тары {memory.number}: исключена ссылка без подтверждённого заезда",
            payload={
                "migration": "0019_confirmed_entry_tare_memory",
                "wagon_number": memory.number,
                "previous_record_id": memory.record_id,
                "record_id": None,
                "reason": "no_confirmed_entry_source",
                "before": before,
                "after": None,
            },
        )
        memory.delete(using=alias)


class Migration(migrations.Migration):
    dependencies = [
        ("grain", "0018_vehicle_tare_memory"),
        ("eventlog", "0003_eventlog_eventlog_recent_idx_and_more"),
    ]
    operations = [migrations.RunPython(seed_confirmed_entry_tares, migrations.RunPython.noop)]
