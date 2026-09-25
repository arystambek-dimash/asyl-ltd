"""Confirmed entry tare memory with preserved measured/manual provenance."""

import re

from django.db import transaction
from django.db.models import F, Func, Q, Value
from django.db.models.functions import Length

from . import services, statuses as st
from .manual_passages import MANUAL_REASON_MAX_LENGTH, MANUAL_REASON_MIN_LENGTH, clean_reason
from .models import UnassignedWeighing, VehicleTareMemory, Wagon, WeighingRecord


def confirmed_sources(number):
    """Only actual scale entries or explicitly staff-confirmed manual entries.

    Empty camera orientation is not an entry verdict on its own. A staff
    operator may confirm that scale record as an entry; an unattended unknown
    frame, a rear frame, an AI value or a historical reuse never qualifies.
    """
    human = Q(operator__isnull=False, operator__is_client=False)
    measured = Q(source="scale") & (Q(orientation="front") | (Q(orientation="") & human))
    manual = Q(
        source="manual",
        _reason_length__gte=MANUAL_REASON_MIN_LENGTH,
        _reason_length__lte=MANUAL_REASON_MAX_LENGTH,
    ) & human
    rows = WeighingRecord.objects.select_related("wagon", "operator", "reference_record").filter(
        wagon__direction=Wagon.PASSAGE, kind="gross", reference_record__isnull=True,
    ).exclude(orientation="rear").annotate(
        _reason_length=Length(Func(
            F("manual_reason"), Value(r"^\s+|\s+$"), Value(""), Value("g"),
            function="REGEXP_REPLACE",
        )),
    ).filter(measured | manual).order_by("-created_at", "-pk")
    number = services.normalize_passage_number(number)
    if not services.KZ_VEHICLE_PLATE_RE.fullmatch(number):
        return rows.none()
    # Older records may contain spaces/hyphens. Compare the same canonical
    # plate without altering the historical trip or its original record.
    plate_pattern = r"^[\s-]*" + r"[\s-]*".join(re.escape(char) for char in number) + r"[\s-]*$"
    return rows.filter(wagon__number__iregex=plate_pattern)


@transaction.atomic
def remember(record, number):
    number = services.normalize_passage_number(number)
    if not services.KZ_VEHICLE_PLATE_RE.fullmatch(number):
        return
    if not confirmed_sources(number).filter(pk=record.pk).exists():
        return
    memory, created = VehicleTareMemory.objects.select_for_update().get_or_create(
        number=number, defaults={"record": record, "observed_at": record.created_at}
    )
    if not created and (
        (record.created_at, record.pk) > (memory.observed_at, memory.record_id)
        or not confirmed_sources(number).filter(pk=memory.record_id).exists()
    ):
        memory.record, memory.observed_at = record, record.created_at
        memory.save(update_fields=["record", "observed_at", "updated_at"])
    return memory


def latest_before(item, number):
    """Latest confirmed entry BEFORE exit, never an older, lighter substitute."""
    latest = confirmed_sources(number).filter(created_at__lt=item.stable_weight_at).first()
    if latest is not None:
        remember(latest, number)
    return latest


def candidates(item, number):
    rows = confirmed_sources(number).filter(created_at__lt=item.stable_weight_at)
    latest_id = rows.values_list("pk", flat=True).first()
    # The latest tare remains decisive even when it contradicts exit weight.
    # Do not offer an older lighter entry that would hide the contradiction.
    return rows.filter(pk=latest_id, weight_kg__lt=item.weight_kg)


@transaction.atomic
def complete(item, user, *, reference_record, number, reason, automatic=False):
    number = services.normalize_passage_number(number)
    if not number:
        raise services.validation_error("Укажите госномер машины", "bad_vehicle_number")
    reason = clean_reason(reason)
    # Same lock order as automatic assignment: lane -> event -> wagon.
    services.lock_passage_lane()
    item = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if item.vehicle_number and services.normalize_passage_number(item.vehicle_number) != number:
        raise services.validation_error("Номер не совпадает с сохранённым взвешиванием", "tare_plate_mismatch")
    if item.status == UnassignedWeighing.ASSIGNED and item.action == "exit":
        if item.wagon.number == number and item.wagon.weighings.filter(
            kind="gross", source="historical", reference_record_id=reference_record
        ).exists():
            return item
        raise services.validation_error("Выезд уже обработан иначе", "tare_already_resolved")
    source = confirmed_sources(number).filter(
        created_at__lt=item.stable_weight_at,
    ).select_for_update(of=("self",)).first()
    if source is None or source.pk != reference_record or source.weight_kg >= item.weight_kg:
        raise services.validation_error("Нужна последняя подтверждённая тара этой машины до выезда", "tare_source_invalid")
    if item.orientation == "front" or item.status == UnassignedWeighing.DISCARDED:
        raise services.validation_error("Это взвешивание нельзя оформить как выезд", "tare_exit_invalid")
    previous_kind = None
    if item.status == UnassignedWeighing.ASSIGNED:
        wagon = Wagon.objects.select_for_update().get(pk=item.wagon_id)
        records = list(wagon.weighings.select_for_update())
        if not (
            item.action == "entry" and wagon.is_passage and wagon.number == number
            and wagon.status == st.AT_SILO and wagon.tare_weight_kg is None
            and len(records) == 1 and records[0].kind == "gross"
            and records[0].source == "scale" and item.photo_request_id
            and records[0].photo_request_id == item.photo_request_id
            and records[0].weight_kg == item.weight_kg
        ):
            raise services.validation_error("Рейс уже изменён: нужна отдельная проверка", "tare_correction_conflict")
        exit_record = records[0]
        previous_kind = exit_record.kind
        exit_record.kind = "tare"
        exit_record.created_at = item.stable_weight_at
        exit_record.save(update_fields=["kind", "created_at"])
    elif item.status == UnassignedWeighing.OPEN:
        if automatic:
            wagon = services.open_camera_passage(number, item, cargo_name=source.wagon.cargo_name)
        else:
            wagon = services.create_passage(user, number=number, cargo_name=source.wagon.cargo_name)
        services.set_status(
            wagon, st.AT_SILO, user,
            f"Вывоз {number}: заезд не записан, тара {source.weight_kg} кг из прежнего взвешивания",
            entry_weight_kg=source.weight_kg, reference_record_id=source.pk,
        )
        services.record_weighing(wagon, "tare", item.weight_kg, user, occurred_at=item.stable_weight_at, **services.unassigned_scale_kwargs(item))
        exit_record = wagon.weighings.get(kind="tare")
    else:
        raise services.validation_error("Взвешивание уже обработано", "tare_already_resolved")
    reference = WeighingRecord.objects.create(
        wagon=wagon, kind="gross", weight_kg=source.weight_kg, source="historical",
        reference_record=source, operator=user, manual_reason=reason,
        photo=source.photo.name, photo_camera=source.photo_camera, orientation=source.orientation,
    )
    # The arrival was not observed. Do not invent yesterday/today's entry time.
    wagon.arrived_at = item.stable_weight_at
    wagon.silo_arrived_at = None
    wagon.unloading_started_at = None
    wagon.gross_weight_kg = source.weight_kg
    wagon.save(update_fields=["arrived_at", "silo_arrived_at", "unloading_started_at", "gross_weight_kg"])
    services.log_wagon_event(
        wagon, "tare_reference", f"Вывоз {number}: тара {source.weight_kg} кг из прежнего взвешивания; {reason}",
        user, unassigned_id=item.pk, reference_record_id=source.pk,
        reference_record_at=source.created_at.isoformat(), reference_trip_id=source.wagon_id,
        reference_record_source=source.source,
        reference_weight_kg=source.weight_kg, new_record_id=reference.pk,
        exit_record_id=exit_record.pk, previous_kind=previous_kind,
        exit_weight_kg=item.weight_kg, exit_at=item.stable_weight_at.isoformat(), reason=reason,
        auto=automatic,
    )
    services.finish_passage_exit(wagon, item.weight_kg, user, occurred_at=item.stable_weight_at)
    services.resolve_unassigned_weighing(item, wagon, "exit", user)
    services.bind_passage_capture(item)
    return item
