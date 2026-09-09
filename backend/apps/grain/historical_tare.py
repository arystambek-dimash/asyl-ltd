"""Measured tare memory and audited manual/automatic reuse of its source record."""

from django.db import transaction
from django.utils import timezone

from . import services, statuses as st
from .models import PassageScaleAutomationState, UnassignedWeighing, VehicleTareMemory, Wagon, WeighingRecord


def measured_sources(number):
    return WeighingRecord.objects.select_related("wagon").filter(
        wagon__direction=Wagon.PASSAGE, wagon__number=number,
        kind="gross", source="scale", reference_record__isnull=True,
        orientation="front",
    ).order_by("-created_at", "-id")


@transaction.atomic
def remember(record, number):
    number = services.normalize_passage_number(number)
    if not services.KZ_VEHICLE_PLATE_RE.fullmatch(number):
        return
    if record.kind != "gross" or record.source != "scale" or record.orientation != "front" or record.reference_record_id:
        return
    memory, created = VehicleTareMemory.objects.select_for_update().get_or_create(
        number=number, defaults={"record": record, "observed_at": record.created_at}
    )
    if not created and (record.created_at, record.pk) > (memory.observed_at, memory.record_id):
        memory.record, memory.observed_at = record, record.created_at
        memory.save(update_fields=["record", "observed_at", "updated_at"])
    return memory


def latest_before(item, number):
    """Choose latest real measurement BEFORE this exit, even during replay.

    Do not select an older, lighter weight when the latest tare exceeds the
    departure weight. That contradiction must remain visible for review.
    """
    memory = VehicleTareMemory.objects.select_related("record__wagon").filter(
        number=number, observed_at__lt=item.stable_weight_at,
    ).first()
    latest = measured_sources(number).filter(created_at__lt=item.stable_weight_at).first()
    if memory is not None:
        record = memory.record
        if (
            record.kind == "gross" and record.source == "scale" and record.orientation == "front"
            and record.reference_record_id is None
            and services.normalize_passage_number(record.wagon.number) == number
            and (latest is None or (record.created_at, record.pk) > (latest.created_at, latest.pk))
        ):
            latest = record
    if latest is not None:
        remember(latest, number)
    return latest


def candidates(item, number):
    number = services.normalize_passage_number(number)
    if not number:
        return WeighingRecord.objects.none()
    return WeighingRecord.objects.select_related("wagon", "operator").filter(
        wagon__direction=Wagon.PASSAGE, wagon__number=number,
        kind="gross", source="scale", reference_record__isnull=True,
        orientation="front", created_at__lt=item.stable_weight_at,
        weight_kg__lt=item.weight_kg,
    ).exclude(photo="").exclude(photo__isnull=True).order_by("-created_at", "-id")


@transaction.atomic
def complete(item, user, *, reference_record, number, reason, automatic=False):
    number = services.normalize_passage_number(number)
    reason = str(reason or "").strip()
    if not number or not 5 <= len(reason) <= 300:
        raise services._error("Укажите номер и причину (5–300 символов)", "tare_reason_required")
    # Same lock order as automatic assignment: lane -> event -> wagon.
    PassageScaleAutomationState.objects.select_for_update().filter(scale_number="truck").first()
    item = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if item.vehicle_number and services.normalize_passage_number(item.vehicle_number) != number:
        raise services._error("Номер не совпадает с сохранённым взвешиванием", "tare_plate_mismatch")
    if item.status == UnassignedWeighing.ASSIGNED and item.action == "exit":
        if item.wagon.number == number and item.wagon.weighings.filter(
            kind="gross", source="historical", reference_record_id=reference_record
        ).exists():
            return item
        raise services._error("Выезд уже обработан иначе", "tare_already_resolved")
    eligible = (WeighingRecord.objects.select_related("wagon").filter(
        wagon__direction=Wagon.PASSAGE, kind="gross", source="scale",
        orientation="front", reference_record__isnull=True,
    ) if automatic else candidates(item, number))
    source = eligible.filter(created_at__lt=item.stable_weight_at, weight_kg__lt=item.weight_kg).select_for_update(of=("self",)).filter(pk=reference_record).first()
    if source is None or services.normalize_passage_number(source.wagon.number) != number:
        raise services._error("Нужна прежняя измеренная тара этой машины с фото спереди", "tare_source_invalid")
    if item.orientation == "front" or item.status == UnassignedWeighing.DISCARDED:
        raise services._error("Это взвешивание нельзя оформить как выезд", "tare_exit_invalid")
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
            raise services._error("Рейс уже изменён: нужна отдельная проверка", "tare_correction_conflict")
        exit_record = records[0]
        previous_kind = exit_record.kind
        exit_record.kind = "tare"
        exit_record.created_at = item.stable_weight_at
        exit_record.save(update_fields=["kind", "created_at"])
    elif item.status == UnassignedWeighing.OPEN:
        if automatic:
            wagon = Wagon.objects.create(
                direction=Wagon.PASSAGE, workflow="simple", number=number,
                cargo_name=source.wagon.cargo_name, status=st.ARRIVED,
                arrived_at=item.stable_weight_at, number_source="camera",
                number_camera_source=item.camera,
            )
        else:
            wagon = services.create_passage(user, number=number, cargo_name=source.wagon.cargo_name)
        wagon.status = st.AT_SILO
        services._record_weighing(wagon, "tare", item.weight_kg, user, occurred_at=item.stable_weight_at, **services._unassigned_scale_kwargs(item))
        services._move_unassigned_photo(item, wagon, "tare")
        exit_record = wagon.weighings.get(kind="tare")
    else:
        raise services._error("Взвешивание уже обработано", "tare_already_resolved")
    reference = WeighingRecord.objects.create(
        wagon=wagon, kind="gross", weight_kg=source.weight_kg, source="historical",
        reference_record=source, operator=user, manual_reason=reason,
        photo=source.photo.name, photo_camera=source.photo_camera, orientation="front",
    )
    # The arrival was not observed. Do not invent yesterday/today's entry time.
    wagon.arrived_at = item.stable_weight_at
    wagon.silo_arrived_at = None
    wagon.unloading_started_at = None
    wagon.gross_weight_kg = source.weight_kg
    wagon.save(update_fields=["status", "arrived_at", "silo_arrived_at", "unloading_started_at", "gross_weight_kg"])
    services._log(
        wagon, "tare_reference", f"Вывоз {number}: тара {source.weight_kg} кг из прежнего взвешивания; {reason}",
        user, unassigned_id=item.pk, reference_record_id=source.pk,
        reference_record_at=source.created_at.isoformat(), reference_trip_id=source.wagon_id,
        reference_weight_kg=source.weight_kg, new_record_id=reference.pk,
        exit_record_id=exit_record.pk, previous_kind=previous_kind,
        exit_weight_kg=item.weight_kg, exit_at=item.stable_weight_at.isoformat(), reason=reason,
        auto=automatic,
    )
    services._finish_passage_exit(wagon, item.weight_kg, user, occurred_at=item.stable_weight_at)
    item.status, item.action, item.wagon = UnassignedWeighing.ASSIGNED, "exit", wagon
    item.resolved_by, item.resolved_at = user, timezone.now()
    item.save(update_fields=["status", "action", "wagon", "resolved_by", "resolved_at"])
    if item.capture_id:
        item.capture.__class__.objects.filter(pk=item.capture_id).update(wagon=wagon, action="exit")
    return item
