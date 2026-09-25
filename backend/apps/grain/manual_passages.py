"""Audited recovery of outbound trips without inventing camera observations.

Manual records supplement the scale ledger; corrections never overwrite a
physical measurement, its timestamp, or its photograph.
"""

import re

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from . import services, statuses as st
from .models import (
    UnassignedWeighing,
    Wagon,
    WeighingIdentityCheck,
)

# A manual weighing's reason. Only a manual entry within these bounds counts
# as confirmed tare (historical_tare.confirmed_sources).
MANUAL_REASON_MIN_LENGTH = 5
MANUAL_REASON_MAX_LENGTH = 300


def whole_kg(value, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not (
        isinstance(value, int)
        or (isinstance(value, str) and re.fullmatch(r"[0-9]+", value))
    ):
        raise services.validation_error("Вес должен быть целым числом килограммов", "bad_weight")
    weight = int(value)
    if not 0 < weight <= 2**63 - 1:
        raise services.validation_error("Вес должен быть положительным целым числом", "bad_weight")
    return weight


def clean_reason(value):
    reason = str(value or "").strip()
    if not MANUAL_REASON_MIN_LENGTH <= len(reason) <= MANUAL_REASON_MAX_LENGTH:
        raise services.validation_error(
            f"Укажите причину ({MANUAL_REASON_MIN_LENGTH}–{MANUAL_REASON_MAX_LENGTH} символов)",
            "manual_reason_required",
        )
    return reason


def _exit_snapshot(wagon):
    """The exit side of the trip, as the audit records it before and after a correction."""
    return {
        "exit_weight_kg": wagon.tare_weight_kg,
        "net_weight_kg": wagon.net_weight_kg,
        "status": wagon.status,
        "exited_at": wagon.exited_at.isoformat() if wagon.exited_at else None,
    }


@transaction.atomic
def create_entry(
    user, *, number, cargo_name, entry_weight_kg, arrived_at, reason,
    unassigned_weighing=None,
):
    reason = clean_reason(reason)
    weight = whole_kg(entry_weight_kg)
    number = services.normalize_passage_number(number)
    if not services.KZ_VEHICLE_PLATE_RE.fullmatch(number):
        raise services.validation_error("Укажите полный госномер машины", "bad_vehicle_number")
    cargo_name = str(cargo_name or "").strip()
    if not cargo_name or len(cargo_name) > 100:
        raise services.validation_error("Укажите груз (до 100 символов)", "cargo_required")
    now = timezone.now()
    if arrived_at is None or timezone.is_naive(arrived_at) or arrived_at > now:
        raise services.validation_error("Укажите прошедшее время заезда", "bad_arrival_time")

    state = services.lock_passage_lane_for_manual_operation()
    item = None
    if unassigned_weighing is not None:
        item = UnassignedWeighing.objects.select_for_update().filter(pk=unassigned_weighing).first()
        if item is None:
            raise NotFound("Взвешивание не найдено")
        if item.status != UnassignedWeighing.OPEN:
            raise services.validation_error("Это взвешивание уже обработано", "unassigned_weighing_resolved")
        if item.orientation == "front":
            raise services.validation_error("Камера определила заезд, а не выезд", "manual_exit_orientation_conflict")
        if item.vehicle_number and services.normalize_passage_number(item.vehicle_number) != number:
            raise services.validation_error("Номер не совпадает с сохранённым взвешиванием", "manual_exit_plate_mismatch")
        if arrived_at >= item.stable_weight_at:
            raise services.validation_error("Заезд должен быть раньше сохранённого выезда", "passage_time_conflict")
        if weight >= item.weight_kg:
            raise services.validation_error("Вес выезда должен быть больше веса заезда", "bad_exit_weight")

    same_plate = Wagon.objects.select_for_update().filter(direction=Wagon.PASSAGE, number=number)
    if same_plate.filter(status__in=st.ON_SITE_STATUSES).exists():
        raise services.validation_error(f"Машина {number} уже находится на территории", "passage_already_on_site")
    if same_plate.filter(arrived_at=arrived_at).exists():
        raise services.validation_error("Заезд с этим номером и временем уже записан", "manual_entry_already_recorded")

    try:
        with transaction.atomic():
            wagon = Wagon.objects.create(
                direction=Wagon.PASSAGE, workflow="simple", number=number,
                cargo_name=cargo_name, status=st.ARRIVED, arrived_at=arrived_at,
                arrived_by=user, number_source="manual",
            )
    except IntegrityError as exc:
        raise services.validation_error("Для этой машины уже создан открытый рейс", "passage_already_on_site") from exc
    services.record_passage_entry_weight(
        wagon, weight, user, source="manual", manual_reason=reason,
        occurred_at=arrived_at,
    )
    services.log_wagon_event(
        wagon, "manual_entry", f"Вывоз {number}: вручную восстановлен заезд без фото; {reason}",
        user, reason=reason, before=None,
        after={"entry_weight_kg": weight, "arrived_at": arrived_at.isoformat()},
        unassigned_id=item.pk if item else None,
        entry_record_id=wagon.weighings.get(kind="gross").pk,
        source="manual",
    )
    if item is not None:
        item = services.assign_unassigned_weighing(item, wagon, user)
        services.bind_passage_capture(item)
        # An already-running GPT request cannot publish a late identity over
        # this explicit operator recovery. Its response remains untrusted.
        WeighingIdentityCheck.objects.filter(weighing=item).update(
            status=WeighingIdentityCheck.MATCHED, reason="manual_entry_recovered", lease_until=None,
        )
    services.fence_automatic_passage_lane_for_manual_mutation(state)
    wagon.refresh_from_db()
    return wagon


@transaction.atomic
def correct_exit(user, wagon, *, exit_weight_kg, expected_exit_weight_kg, reason):
    reason = clean_reason(reason)
    weight = whole_kg(exit_weight_kg)
    expected = whole_kg(expected_exit_weight_kg, nullable=True)
    state = services.lock_passage_lane_for_manual_operation()
    services.lock_wagon(wagon)
    if not wagon.is_passage:
        raise services.validation_error("Исправление доступно только для вывоза", "not_passage")
    if wagon.tare_weight_kg != expected:
        raise services.validation_error("Выездной вес уже изменён. Обновите рейс и проверьте данные", "exit_weight_conflict")
    completing = wagon.status == st.AT_SILO and wagon.tare_weight_kg is None
    correcting = wagon.status == st.COMPLETED and wagon.tare_weight_kg is not None
    if not (completing or correcting):
        raise services.validation_error("Рейс сейчас не допускает исправление выездного веса", "passage_state_mismatch")
    if wagon.gross_weight_kg is None:
        raise services.validation_error("Сначала запишите вес заезда", "entry_weight_required")
    if weight <= wagon.gross_weight_kg:
        raise services.validation_error("Вес выезда должен быть больше веса заезда", "bad_exit_weight")
    if weight == expected:
        raise services.validation_error("Выездной вес не изменился", "exit_weight_unchanged")
    before = _exit_snapshot(wagon)
    services.record_weighing(wagon, "tare", weight, user, source="manual", manual_reason=reason)
    record = wagon.weighings.order_by("-pk").first()
    if completing:
        services.finish_passage_exit(wagon, weight, user)
    else:
        # Completion timestamps and all immutable scale records are retained.
        # Outbound passages do not affect grain inventory or silo balances.
        wagon.tare_weight_kg = weight
        wagon.net_weight_kg = wagon.computed_net_kg()
        wagon.save(update_fields=["tare_weight_kg", "net_weight_kg"])
    services.log_wagon_event(
        wagon, "exit_weight_corrected", f"{wagon}: выездной вес исправлен вручную на {weight} кг; {reason}",
        user, reason=reason, before=before, after=_exit_snapshot(wagon),
        weighing_record_id=record.pk,
    )
    services.fence_automatic_passage_lane_for_manual_mutation(state)
    return wagon
