"""Бизнес-операции прихода зерна. Статусы меняются только здесь.

Каждая операция атомарна, силос блокируется ``select_for_update`` перед
резервом и оприходованием — два вагона не займут одно и то же место.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, InterfaceError, OperationalError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.cameras.ai import VEHICLE_PLATE_RE as KZ_VEHICLE_PLATE_RE
from apps.cameras.models import VehiclePlateEvent
from apps.common.plates import normalize_plate
from apps.eventlog.services import log_event

from . import scale
from . import statuses as st
from .models import (
    AutomaticPassageCapture,
    GrainMovement,
    GrainSupply,
    LabCheck,
    PassageScaleAutomationState,
    PassageWeightCapture,
    Silo,
    SiloAllocation,
    SiloReservation,
    UnassignedWeighing,
    Wagon,
    WeighingRecord,
    VEHICLE_ORIENTATION_FRONT,
    VEHICLE_ORIENTATION_REAR,
)
from .queries import silo_overview

VEHICLE_PLATE_CAMERA = "cam1"
VEHICLE_PLATE_SOURCE = "main"
VEHICLE_PLATE_MAX_AGE = timedelta(minutes=5)
VEHICLE_PLATE_MAX_FUTURE = timedelta(minutes=1)
VEHICLE_PLATE_CANDIDATE_LIMIT = 5

AUTO_ACTION_ENTRY = "entry"
AUTO_ACTION_EXIT = "exit"
AUTO_ACTION_IGNORED = "ignored"
AUTO_ACTION_MANUAL_ENTRY = "manual_entry"
AUTO_ACTION_UNASSIGNED = "unassigned"


def validation_error(detail: str, code: str) -> ValidationError:
    return ValidationError({"detail": detail, "code": code})


def lock_wagon(wagon: Wagon) -> None:
    """Refresh the caller's instance only after acquiring its row lock.

    Classic intake callers reuse the same instance between commands. Keep
    that contract while validating every command against committed state.
    The caller must hold an atomic transaction until the command completes.
    """
    try:
        Wagon.objects.select_for_update().only("pk").get(pk=wagon.pk)
        wagon.refresh_from_db()
    except Wagon.DoesNotExist as exc:
        raise NotFound("Вагон не найден") from exc


def log_wagon_event(wagon: Wagon, event: str, message: str, user, **payload):
    log_event(
        f"grain_{event}",
        message,
        user=user,
        payload={
            "wagon_id": wagon.pk,
            "wagon_number": wagon.number,
            "supply_id": wagon.supply_id,
            "status": wagon.status,
            **payload,
        },
    )


def ensure_transition(wagon: Wagon, target: str):
    if not st.can_transition(wagon.status, target, passage=wagon.is_passage):
        current = st.WAGON_STATUS_LABELS.get(wagon.status, wagon.status)
        wanted = st.WAGON_STATUS_LABELS.get(target, target)
        raise validation_error(
            f"Переход «{current} → {wanted}» недопустим",
            "invalid_wagon_transition",
        )


def set_status(wagon: Wagon, target: str, user, message: str, **payload):
    ensure_transition(wagon, target)
    old = wagon.status
    wagon.status = target
    wagon.save(update_fields=["status"])
    log_wagon_event(wagon, "status", message, user, old_status=old, new_status=target, **payload)


# ── Поставки ────────────────────────────────────────────────────────────────


@transaction.atomic
def prepare_simple_supply(supply: GrainSupply, user) -> GrainSupply:
    """Создать новый короткий приход с одним поездом и заранее заданным силосом."""
    if not supply.grain_type_id:
        raise validation_error("Выберите тип зерна", "grain_type_required")
    if not supply.assigned_silo_id:
        raise validation_error("Выберите силос назначения", "silo_required")
    expected = int(supply.expected_total_kg or 0)
    if expected <= 0:
        raise validation_error("Укажите ожидаемый вес", "expected_weight_required")

    silo = Silo.objects.select_for_update().get(pk=supply.assigned_silo_id)
    if silo.status != "active":
        raise validation_error("Выбранный силос недоступен", "silo_inactive")
    if silo.silo_type_id and silo.silo_type_id != supply.grain_type_id:
        raise validation_error(
            "Тип зерна не совпадает с типом выбранного силоса",
            "grain_type_silo_mismatch",
        )
    if silo.free_capacity_kg < expected:
        raise validation_error(
            f"В силосе «{silo.name}» недостаточно свободного места",
            "insufficient_capacity",
        )

    wagon = Wagon.objects.create(
        supply=supply,
        number="",
        status=st.EXPECTED,
        workflow="simple",
        number_source="camera",
        expected_weight_kg=expected,
        assigned_silo=silo,
        unloading_point=silo.unloading_line,
    )
    SiloReservation.objects.create(
        wagon=wagon,
        silo=silo,
        amount_kg=expected,
    )
    supply.status = "expected"
    supply.save(update_fields=["status"])
    log_wagon_event(
        wagon,
        "supply",
        f"Создан приход #{supply.pk}: ожидается поезд в силос «{silo.name}»",
        user,
        grain_type_id=supply.grain_type_id,
        silo_id=silo.pk,
        expected_weight_kg=expected,
    )
    return supply


# ── Прибытие ────────────────────────────────────────────────────────────────


@transaction.atomic
def register_arrival(number: str, user, supply: GrainSupply | None) -> Wagon:
    """Привязать номер к заранее созданному рейсу короткого прихода."""
    number = (number or "").strip()
    if not number:
        raise validation_error("Укажите номер вагона", "wagon_number_required")
    if supply is None:
        raise validation_error("Выберите ожидаемый приход", "supply_required")
    if Wagon.objects.filter(
        number=number,
        status__in=st.ON_SITE_STATUSES,
    ).exists():
        raise validation_error(
            f"Вагон {number} уже зарегистрирован на территории",
            "wagon_already_on_site",
        )

    # Поезд короткого прихода создан заранее без номера: оператор заполняет
    # его при фактическом въезде, не создавая дубликат поставки.
    wagon = (
        supply.wagons.select_for_update()
        .filter(workflow="simple", number="", status=st.EXPECTED)
        .order_by("id")
        .first()
    )
    if wagon is None:
        # У короткого прихода ровно один рейс (prepare_simple_supply).
        raise validation_error(
            f"По приходу #{supply.pk} вагон уже прибыл",
            "supply_already_arrived",
        )
    wagon.number = number
    wagon.number_source = "manual"
    wagon.number_camera_source = ""
    wagon.arrived_at = timezone.now()
    wagon.arrived_by = user
    wagon.save(
        update_fields=[
            "number",
            "number_source",
            "number_camera_source",
            "arrived_at",
            "arrived_by",
        ]
    )
    log_wagon_event(
        wagon,
        "arrival",
        f"Номер {number} привязан к приходу #{supply.pk}",
        user,
    )
    set_status(wagon, st.ARRIVED, user, f"Вагон {number} прибыл")
    return wagon


# ── Взвешивания ─────────────────────────────────────────────────────────────


def record_weighing(
    wagon: Wagon,
    kind: str,
    weight_kg: int,
    user,
    *,
    scale_number="",
    source="manual",
    manual_reason="",
    scale_age_seconds=None,
    scale_updated_at=None,
    photo_request_id=None,
    photo_camera="",
    orientation="",
    occurred_at=None,
):
    try:
        weight_kg = int(weight_kg)
    except (TypeError, ValueError):
        raise validation_error("Вес должен быть целым числом килограммов", "bad_weight")
    if weight_kg <= 0:
        raise validation_error("Вес должен быть положительным", "bad_weight")
    if source == "manual" and not manual_reason:
        raise validation_error("Для ручного ввода веса укажите причину", "manual_reason_required")
    previous = wagon.gross_weight_kg if kind == "gross" else wagon.tare_weight_kg
    record = WeighingRecord.objects.create(
        wagon=wagon,
        kind=kind,
        weight_kg=weight_kg,
        scale_number=scale_number,
        source=source,
        manual_reason=manual_reason,
        previous_weight_kg=previous,
        operator=user,
        # Сам кадр подтягивается после фиксации веса по этому идентификатору.
        photo_request_id=photo_request_id,
        photo_camera=photo_camera or "",
        orientation=orientation or "",
    )
    if occurred_at is not None:
        # Deferred delivery keeps the physical measurement time. EventLog
        # still records when this database operation was performed.
        WeighingRecord.objects.filter(pk=record.pk).update(created_at=occurred_at)
        record.created_at = occurred_at
    if wagon.is_passage and kind == "gross":
        from .historical_tare import remember
        remember(record, wagon.number)
    scale_payload = {}
    if occurred_at is not None:
        scale_payload["occurred_at"] = occurred_at.isoformat()
    if scale_age_seconds is not None:
        scale_payload["scale_age_seconds"] = str(scale_age_seconds)
    if scale_updated_at is not None:
        scale_payload["scale_updated_at"] = scale_updated_at
    if wagon.is_passage:
        label = "вес пустой на въезде" if kind == "gross" else "вес гружёной на выезде"
        message = f"{wagon}: {label} {weight_kg} кг"
    else:
        message = f"Вагон {wagon.number}: {'брутто' if kind == 'gross' else 'тара'} {weight_kg} кг"
    log_wagon_event(
        wagon,
        "weighing",
        message,
        user,
        kind=kind,
        weight_kg=weight_kg,
        source=source,
        scale_number=scale_number,
        previous_weight_kg=previous,
        manual_reason=manual_reason,
        **scale_payload,
    )
    return weight_kg


def whole_scale_weight_kg(reading: scale.ScaleReading) -> int:
    """Round a strict Decimal scale sample to the grain model's whole kg."""
    weight = reading.weight_kg.to_integral_value(rounding=ROUND_HALF_UP)
    if weight <= 0:
        raise scale.TruckScaleNotReady(
            "Показание весов после округления должно быть не меньше 1 кг."
        )
    return int(weight)


def _scale_kwargs(reading: scale.ScaleReading, scale_key: str) -> dict:
    """Поля журнала взвешивания для показания физических весов."""
    return {
        "source": "scale",
        "scale_number": scale_key,
        "scale_age_seconds": reading.age_seconds,
        "scale_updated_at": reading.updated_at,
    }


def ensure_scale_action_ready(wagon: Wagon, action: str) -> None:
    """Reject stale or mismatched commands before contacting the scale."""
    targets = {"entry": st.AT_SILO, "exit": st.TARE_WEIGHED}
    try:
        target = targets[action]
    except KeyError as exc:
        raise ValueError(f"Unknown grain scale action: {action}") from exc

    if wagon.workflow != "simple":
        raise validation_error(
            "Команда взвешивания не соответствует маршруту вагона",
            "wrong_scale_action",
        )
    ensure_transition(wagon, target)


def record_scale_weight(
    wagon: Wagon,
    action: str,
    user,
) -> Wagon:
    """Read the physical scale, then atomically apply one weighing command.

    Intake I/O happens before its database transaction. Passage I/O holds only
    the singleton automatic-lane mutex (not the Wagon row) for the bounded
    scale request so the background edge detector cannot re-arm mid-command.
    The write phase locks and reloads the Wagon in both cases.
    """
    ensure_scale_action_ready(wagon, action)
    if wagon.is_passage:
        with transaction.atomic():
            _prepare_manual_passage_scale_operation()
            return _read_and_store_scale_weight(wagon, action, user)
    return _read_and_store_scale_weight(wagon, action, user)


def _read_and_store_scale_weight(wagon: Wagon, action: str, user) -> Wagon:
    expected_status = wagon.status
    scale_key = scale.TRUCK_SCALE_KEY if wagon.is_passage else scale.WAGON_SCALE_KEY
    with scale.authoritative_capture(scale_key):
        reading = scale.read_truck_scale(scale_key)
        try:
            return _store_scale_weight(
                wagon.pk,
                action,
                reading,
                user,
                expected_status=expected_status,
                scale_key=scale_key,
            )
        except (OperationalError, InterfaceError) as exc:
            raise scale.TruckScaleApplyUnavailable() from exc


@transaction.atomic
def _store_scale_weight(
    wagon_id: int,
    action: str,
    reading: scale.ScaleReading,
    user,
    *,
    expected_status: str,
    scale_key: str,
) -> Wagon:
    scale.configure_authoritative_db_timeouts()
    # Lock only the wagon row. Nullable joins cannot be locked by PostgreSQL,
    # and related objects are loaded lazily where a transition needs them.
    wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon_id)
    if wagon.status != expected_status:
        raise validation_error(
            "Состояние вагона изменилось во время чтения весов — повторите взвешивание",
            "wagon_changed_during_scale_read",
        )
    # Неизвестную команду уже отверг ensure_scale_action_ready: остаются
    # entry/exit.
    ensure_scale_action_ready(wagon, action)
    kwargs = _scale_kwargs(reading, scale_key)
    weight_kg = whole_scale_weight_kg(reading)
    if wagon.is_passage:
        record = (
            record_passage_entry_weight
            if action == "entry"
            else record_passage_exit_weight
        )
    else:
        record = (
            record_simple_entry_weight
            if action == "entry"
            else record_simple_exit_weight
        )
    return record(wagon, weight_kg, user, **kwargs)


@transaction.atomic
def record_simple_entry_weight(
    wagon: Wagon,
    weight_kg: int,
    user,
    *,
    occurred_at=None,
    **kwargs,
) -> Wagon:
    """Входные весы → сразу маршрут к заранее назначенному силосу."""
    lock_wagon(wagon)
    if wagon.workflow != "simple":
        raise validation_error("Для вагона используется старый маршрут", "not_simple_flow")
    ensure_transition(wagon, st.AT_SILO)
    if not wagon.assigned_silo_id:
        raise validation_error("Для прихода не назначен силос", "silo_required")
    wagon.gross_weight_kg = record_weighing(wagon, "gross", weight_kg, user, occurred_at=occurred_at, **kwargs)
    # Отложенный импорт стопа арки не сдвигает время рейса на момент импорта.
    wagon.silo_arrived_at = occurred_at or timezone.now()
    wagon.unloading_started_at = wagon.silo_arrived_at
    wagon.save(
        update_fields=["gross_weight_kg", "silo_arrived_at", "unloading_started_at"]
    )
    set_status(
        wagon,
        st.AT_SILO,
        user,
        f"Поезд {wagon.number} взвешен и направлен в силос "
        f"«{wagon.assigned_silo.name}»",
        gross_weight_kg=wagon.gross_weight_kg,
        silo_id=wagon.assigned_silo_id,
    )
    return wagon


# ── Силосы: маршрут прихода ─────────────────────────────────────────────────


def _silo_mismatch(silo: Silo, supply: GrainSupply | None) -> str | None:
    """Код несовместимости силоса с зерном поставки; None — силос подходит.

    Совместимость задаёт тип зерна, как в prepare_simple_supply. Строки
    culture/grain_class сравниваются только у старых записей без типа: у новых
    поставок culture = название типа, и со строкой силоса оно не совпадает.
    """
    if supply is None:
        return None
    if silo.silo_type_id and supply.grain_type_id:
        if silo.silo_type_id != supply.grain_type_id:
            return "grain_type_silo_mismatch"
        return None
    if silo.grain_culture and supply.culture and silo.grain_culture != supply.culture:
        return "silo_culture_mismatch"
    if (silo.grain_class and supply.grain_class
            and silo.grain_class != supply.grain_class and not silo.allow_mixing):
        return "silo_class_mismatch"
    return None


def default_route_silo(wagon: Wagon) -> Silo | None:
    """Силос основного маршрута ★ для типа зерна поставки, если он подходит.

    Подходит активный некарантинный силос совместимого зерна, где хватает
    свободного места под плановый вес рейса.
    """
    supply = wagon.supply
    if supply is None or supply.grain_type_id is None:
        return None
    silo = (
        silo_overview()
        .filter(pk=supply.grain_type.default_silo_id, status="active", is_quarantine=False)
        .first()
    )
    if silo is None or _silo_mismatch(silo, supply) is not None:
        return None
    if silo.free_capacity_kg < (wagon.planned_weight_kg or 0):
        return None
    return silo


@transaction.atomic
def assign_default_silo(wagon: Wagon, user=None) -> Silo | None:
    """Назначить короткому приходу силос ★, если оператор ещё не выбрал свой."""
    lock_wagon(wagon)
    if wagon.assigned_silo_id:
        return wagon.assigned_silo
    silo = default_route_silo(wagon)
    if silo is None:
        return None
    wagon.assigned_silo = silo
    wagon.unloading_point = silo.unloading_line
    wagon.save(update_fields=["assigned_silo", "unloading_point"])
    expected = int(wagon.expected_weight_kg or 0)
    if expected > 0:
        SiloReservation.objects.get_or_create(
            wagon=wagon, defaults={"silo": silo, "amount_kg": expected}
        )
    log_wagon_event(
        wagon,
        "silo",
        f"{wagon}: силос «{silo.name}» назначен по основному маршруту",
        user,
        silo_id=silo.pk,
        auto=True,
    )
    return silo


# ── Выходные весы, нетто и расхождения ─────────────────────────────────────


def _complete_simple_wagon(wagon: Wagon, user, *, occurred_at=None) -> Wagon:
    """Нетто подтверждено: записать приход в силос и сразу закрыть цикл."""
    wagon.unloading_finished_at = occurred_at or timezone.now()
    wagon.save(update_fields=["unloading_finished_at"])
    inventory_wagon(wagon, user)
    wagon.refresh_from_db()
    register_exit(
        wagon,
        user,
        note="Выезд после контрольного взвешивания",
        occurred_at=occurred_at,
    )
    wagon.refresh_from_db()
    return wagon


@transaction.atomic
def record_simple_exit_weight(
    wagon: Wagon,
    weight_kg: int,
    user,
    *,
    occurred_at=None,
    **kwargs,
) -> Wagon:
    """Выходные весы: рассчитать нетто, сверить ожидание и завершить приход."""
    lock_wagon(wagon)
    if wagon.workflow != "simple":
        raise validation_error("Для вагона используется старый маршрут", "not_simple_flow")
    ensure_transition(wagon, st.TARE_WEIGHED)
    tare = record_weighing(wagon, "tare", weight_kg, user, occurred_at=occurred_at, **kwargs)
    if wagon.gross_weight_kg is None:
        raise validation_error("Сначала зафиксируйте входной общий вес", "gross_required")
    if tare >= wagon.gross_weight_kg:
        raise validation_error(
            "Выходной вес не может быть больше или равен входному",
            "bad_tare",
        )
    wagon.tare_weight_kg = tare
    wagon.net_weight_kg = wagon.computed_net_kg()
    wagon.save(update_fields=["tare_weight_kg", "net_weight_kg"])
    set_status(
        wagon,
        st.TARE_WEIGHED,
        user,
        f"Поезд {wagon.number}: выходной вес {tare} кг, нетто {wagon.net_weight_kg} кг",
    )

    if wagon.weight_matches() is False:
        percent = wagon.weight_difference_percent()
        set_status(
            wagon,
            st.WEIGHT_DISCREPANCY,
            user,
            f"Поезд {wagon.number}: нетто отличается от ожидаемого на {percent}%",
            discrepancy_percent=str(percent),
            expected_weight_kg=wagon.planned_weight_kg,
            actual_net_weight_kg=wagon.net_weight_kg,
        )
        return wagon
    return _complete_simple_wagon(wagon, user, occurred_at=occurred_at)


@transaction.atomic
def resolve_simple_discrepancy(
    wagon: Wagon, action: str, user, reason: str = ""
) -> Wagon:
    lock_wagon(wagon)
    if wagon.workflow != "simple" or wagon.status != st.WEIGHT_DISCREPANCY:
        raise validation_error("У прихода нет расхождения для проверки", "no_discrepancy")
    if action == "confirm":
        if not reason:
            raise validation_error("Укажите причину подтверждения", "reason_required")
        set_status(
            wagon,
            st.TARE_WEIGHED,
            user,
            f"Фактическое нетто подтверждено: {reason}",
            resolution="confirmed",
            reason=reason,
        )
        return _complete_simple_wagon(wagon, user)
    if action == "reweigh":
        wagon.tare_weight_kg = None
        wagon.net_weight_kg = None
        wagon.save(update_fields=["tare_weight_kg", "net_weight_kg"])
        set_status(
            wagon,
            st.AT_SILO,
            user,
            f"Поезд {wagon.number} отправлен на повторное выходное взвешивание",
            resolution="reweigh",
        )
        return wagon
    raise validation_error("Неизвестное действие по расхождению", "bad_resolution")


# ── Оприходование ──────────────────────────────────────────────────────────


def _apply_income(silo: Silo, amount_kg: int, wagon: Wagon, user):
    """Записать приход в силос; вызывать только под select_for_update."""
    balance = silo.current_balance_kg
    if balance + amount_kg > silo.total_capacity_kg:
        raise validation_error(
            f"Приход {amount_kg} кг переполнит силос «{silo.name}»",
            "silo_overflow",
        )
    GrainMovement.objects.create(
        silo=silo,
        movement_type="income",
        delta_kg=amount_kg,
        balance_after_kg=balance + amount_kg,
        wagon=wagon,
        supply=wagon.supply,
        batch_number=f"WAGON-{wagon.pk}",
        note=f"Приход из вагона {wagon.number}",
        created_by=user,
    )
    SiloAllocation.objects.create(
        wagon=wagon,
        silo=silo,
        amount_kg=amount_kg,
        operator=user,
    )


@transaction.atomic
def inventory_wagon(wagon: Wagon, user) -> Wagon:
    """Оприходовать нетто в назначенный силос. Идемпотентно: второй раз — ошибка."""
    wagon = Wagon.objects.select_for_update().get(pk=wagon.pk)
    ensure_transition(wagon, st.INVENTORIED)
    if wagon.movements.filter(movement_type="income").exists():
        raise validation_error("Вагон уже оприходован", "already_inventoried")
    if wagon.net_weight_kg is None:
        raise validation_error("Сначала рассчитайте нетто", "net_weight_required")

    if wagon.assigned_silo_id is None:
        raise validation_error("Силос не назначен", "silo_required")
    silo = Silo.objects.select_for_update().get(pk=wagon.assigned_silo_id)
    _apply_income(silo, wagon.net_weight_kg, wagon, user)

    reservation = getattr(wagon, "reservation", None)
    if reservation and reservation.active:
        reservation.active = False
        reservation.save(update_fields=["active"])

    set_status(
        wagon,
        st.INVENTORIED,
        user,
        f"Вагон {wagon.number} оприходован: {wagon.net_weight_kg} кг",
    )
    set_status(wagon, st.EXIT_ALLOWED, user, f"Вагону {wagon.number} разрешён выезд")
    return wagon


# ── Выезд ──────────────────────────────────────────────────────────────────


@transaction.atomic
def register_exit(
    wagon: Wagon,
    user,
    note: str = "",
    *,
    occurred_at=None,
) -> Wagon:
    lock_wagon(wagon)
    ensure_transition(wagon, st.EXITED)
    wagon.exited_at = occurred_at or timezone.now()
    wagon.exit_note = note
    wagon.save(update_fields=["exited_at", "exit_note"])
    set_status(wagon, st.EXITED, user, f"{wagon}: выезд зафиксирован")
    set_status(wagon, st.COMPLETED, user, f"{wagon}: рейс завершён")
    # Different wagons can finish together. Serialize the final completion
    # check so the last committed exit observes the others and closes supply.
    # NO KEY UPDATE allows unrelated FK inserts without a lock upgrade cycle.
    supply = (GrainSupply.objects.select_for_update(no_key=True).get(pk=wagon.supply_id)
              if wagon.supply_id else None)
    if (
        supply
        and not supply.wagons.exclude(
            status__in=st.TERMINAL_STATUSES,
        ).exists()
    ):
        supply.status = "closed"
        supply.save(update_fields=["status"])
    return wagon


# ── Корректировки остатка ──────────────────────────────────────────────────


@transaction.atomic
def adjust_silo(
    silo: Silo,
    delta_kg: int,
    movement_type: str,
    note: str,
    user,
    *,
    supply: GrainSupply | None = None,
    batch_number: str = "",
) -> GrainMovement:
    if movement_type not in (
        "adjustment",
        "inventory_correction",
        "expense",
        "transfer_in",
        "transfer_out",
    ):
        raise validation_error("Недопустимый тип операции", "bad_movement_type")
    if not note:
        raise validation_error("Укажите причину корректировки", "note_required")
    try:
        whole_kg = int(delta_kg)
    except (TypeError, ValueError, OverflowError):
        raise validation_error("Изменение должно быть целым числом кг", "bad_amount")
    # int() молча отбрасывает дробь у числа из JSON (1.5 → 1).
    if not isinstance(delta_kg, str) and whole_kg != delta_kg:
        raise validation_error("Изменение должно быть целым числом кг", "bad_amount")
    delta_kg = whole_kg
    if delta_kg == 0:
        raise validation_error("Изменение не может быть нулевым", "bad_amount")
    silo = Silo.objects.select_for_update().get(pk=silo.pk)
    balance = silo.current_balance_kg
    new_balance = balance + delta_kg
    if new_balance < 0:
        raise validation_error("Остаток силоса не может стать отрицательным", "negative_balance")
    if new_balance > silo.total_capacity_kg:
        raise validation_error("Операция переполнит силос", "silo_overflow")
    movement = GrainMovement.objects.create(
        silo=silo,
        movement_type=movement_type,
        delta_kg=delta_kg,
        balance_after_kg=new_balance,
        supply=supply,
        batch_number=batch_number,
        note=note,
        created_by=user,
    )
    log_event(
        "grain_adjust",
        f"Силос «{silo.name}»: {movement_type} {delta_kg:+} кг ({note})",
        user=user,
        payload={
            "silo_id": silo.pk,
            "delta_kg": delta_kg,
            "movement_type": movement_type,
            "supply_id": supply.pk if supply is not None else None,
            "batch_number": batch_number,
        },
    )
    return movement


# ── Проход: вывоз отрубей ───────────────────────────────────────────────────
# Машина въезжает пустой, грузится и уезжает. Ни силоса, ни лаборатории, ни
# ожидаемого веса: сколько увезут — заранее неизвестно. Фиксируются ровно два
# факта — вес на въезде и вес на выезде, нетто считает Wagon.computed_net_kg.


def _vehicle_plate_time_bounds(now=None):
    current = now or timezone.now()
    return (
        current - VEHICLE_PLATE_MAX_AGE,
        current + VEHICLE_PLATE_MAX_FUTURE,
    )


def _available_vehicle_plate_events(now=None):
    """Fresh, unclaimed webhook events of the truck lane."""

    oldest, newest = _vehicle_plate_time_bounds(now)
    return VehiclePlateEvent.objects.filter(
        camera=VEHICLE_PLATE_CAMERA,
        source=VEHICLE_PLATE_SOURCE,
        processing_status=VehiclePlateEvent.RECEIVED,
        grain_wagon__isnull=True,
        grain_exit_wagon__isnull=True,
        automatic_passage_capture__isnull=True,
        detected_at__gte=oldest,
        detected_at__lte=newest,
        received_at__gte=oldest,
        received_at__lte=newest,
    )


def vehicle_plate_candidates(*, now=None) -> list[VehiclePlateEvent]:
    """Return only fresh, unclaimed events for the configured truck lane."""

    return list(
        _available_vehicle_plate_events(now).order_by("-detected_at", "-id")[
            :VEHICLE_PLATE_CANDIDATE_LIMIT
        ]
    )


def _parse_vehicle_plate_event_id(raw_event_id) -> UUID:
    try:
        return UUID(str(raw_event_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise validation_error(
            "Некорректный идентификатор события номера машины",
            "bad_vehicle_plate_event_id",
        ) from exc


def _locked_vehicle_plate_event(raw_event_id) -> VehiclePlateEvent:
    event_id = _parse_vehicle_plate_event_id(raw_event_id)
    event = (
        VehiclePlateEvent.objects.select_for_update().filter(event_id=event_id).first()
    )
    if event is None:
        raise validation_error(
            "Событие номера машины недоступно",
            "vehicle_plate_event_unavailable",
        )

    if not _available_vehicle_plate_events().filter(pk=event.pk).exists():
        raise validation_error(
            "Событие номера машины недоступно или уже использовано",
            "vehicle_plate_event_unavailable",
        )
    return event


def normalize_passage_number(raw_number) -> str:
    """Canonicalize a Kazakhstan plate without rewriting free-form fallback IDs."""

    # Оператор с русской раскладкой набирает «465 ВСА 13»: номер тот же, что
    # камера читает латиницей, иначе не найдутся ни рейс, ни память тары.
    number = (raw_number or "").strip()
    compact = normalize_plate(number)
    return compact if KZ_VEHICLE_PLATE_RE.fullmatch(compact) else number


def lock_passage_lane() -> PassageScaleAutomationState:
    """Lock the shared truck-lane mutex, creating its row when it is missing.

    Every writer that books, renames or recovers a passage takes this lock
    first: lane -> weighing -> wagon is the one lock order of the grain app.
    """

    state, _created = PassageScaleAutomationState.objects.select_for_update().get_or_create(
        scale_number=scale.TRUCK_SCALE_KEY,
    )
    return state


def lock_passage_lane_for_manual_operation() -> PassageScaleAutomationState:
    """Lock the lane for an operator command and refuse it mid-capture."""

    # Always create the same mutex that automatic_routing.book uses, including
    # when this web worker has its legacy automatic-scale feature flag disabled.
    lock_passage_lane()
    state, capture = lock_automatic_passage_lane()
    assert_automatic_passage_lane_allows_manual_operation(state, capture)
    return state


def reset_passage_lane(state: PassageScaleAutomationState) -> None:
    """Return the lane to UNARMED with no candidate and no current capture."""

    if (
        state.phase == PassageScaleAutomationState.UNARMED
        and state.clear_streak == 0
        and state.stable_streak == 0
        and state.stability_started_at is None
        and state.candidate_weight_kg is None
        and state.current_capture_id is None
    ):
        return
    state.phase = PassageScaleAutomationState.UNARMED
    state.clear_streak = 0
    state.stable_streak = 0
    state.stability_started_at = None
    state.candidate_weight_kg = None
    state.current_capture = None
    state.save(
        update_fields=[
            "phase",
            "clear_streak",
            "stable_streak",
            "stability_started_at",
            "candidate_weight_kg",
            "current_capture",
            "updated_at",
        ]
    )


def lock_automatic_passage_lane() -> tuple[
    PassageScaleAutomationState | None,
    AutomaticPassageCapture | None,
]:
    """Lock the shared automatic lane before any manual passage row lock."""

    if settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        state = lock_passage_lane()
    else:
        # A rolling deploy can temporarily have an enabled monitor and a
        # disabled web worker.  Existing durable lane state remains the mutex
        # regardless of this process's local feature flag; only avoid creating
        # a brand-new state while the feature is disabled.
        state = (
            PassageScaleAutomationState.objects.select_for_update()
            .filter(scale_number=scale.TRUCK_SCALE_KEY)
            .first()
        )
        if state is None:
            return None, None
    capture = (
        AutomaticPassageCapture.objects.select_for_update()
        .filter(pk=state.current_capture_id)
        .only("status", "acknowledged_at", "requires_acknowledgement")
        .first()
        if state.current_capture_id is not None
        else None
    )
    if not settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        processing = (
            capture is not None
            and capture.status == AutomaticPassageCapture.PROCESSING
        )
        unresolved_failure = capture is not None and capture.needs_operator
        safe_terminal = (
            capture is None
            or capture.status == AutomaticPassageCapture.COMPLETED
            or (
                capture.status == AutomaticPassageCapture.FAILED
                and not capture.needs_operator
            )
        )
        if not processing and not unresolved_failure and (
            state.phase
            in {
                PassageScaleAutomationState.UNARMED,
                PassageScaleAutomationState.ARMED,
                PassageScaleAutomationState.STABILIZING,
            }
            or (
                state.phase == PassageScaleAutomationState.AWAITING_CLEAR
                and safe_terminal
            )
        ):
            # The web kill switch must release safe terminal/idle state even
            # when the dedicated monitor is down or still rolling forward.
            reset_passage_lane(state)
            capture = None
    return state, capture


def assert_automatic_passage_lane_allows_manual_operation(
    state: PassageScaleAutomationState | None,
    capture: AutomaticPassageCapture | None,
) -> None:
    blocked = (
        AutomaticPassageCapture.objects.filter(
            status=AutomaticPassageCapture.PROCESSING
        ).exists()
        or (
            capture is not None and capture.status == AutomaticPassageCapture.PROCESSING
        )
        or (
            state is not None
            and state.phase
            in {
                PassageScaleAutomationState.STABILIZING,
                PassageScaleAutomationState.PROCESSING,
            }
        )
        or (
            state is not None
            and state.phase == PassageScaleAutomationState.AWAITING_CLEAR
            and (capture is None or capture.status != AutomaticPassageCapture.FAILED)
        )
    )
    if blocked:
        raise validation_error(
            "Операцию нельзя выполнить, пока автоматические весы "
            "обрабатывают текущую машину.",
            "passage_capture_in_progress",
        )


def fence_automatic_passage_lane_for_manual_mutation(
    state: PassageScaleAutomationState | None,
) -> None:
    """Fence a scale snapshot taken before a successful manual mutation."""

    if state is None or state.phase not in {
        PassageScaleAutomationState.UNARMED,
        PassageScaleAutomationState.ARMED,
    }:
        return
    reset_passage_lane(state)


@transaction.atomic
def _prepare_manual_passage_scale_operation() -> None:
    assert_manual_physical_capture_enabled()
    state, capture = lock_automatic_passage_lane()
    assert_automatic_passage_lane_allows_manual_operation(state, capture)
    fence_automatic_passage_lane_for_manual_mutation(state)


def assert_manual_physical_capture_enabled() -> None:
    from .outbox_importer import enabled

    if enabled():
        raise validation_error(
            "Вес автоматически сохраняет отдельный сборщик. "
            "Дождитесь записи в журнале и привяжите сохранённое взвешивание.",
            "independent_scale_capture_active",
        )


@transaction.atomic
def create_passage(
    user,
    *,
    number="",
    cargo_name="",
    note="",
    vehicle_plate_event_id=None,
) -> Wagon:
    """Зарегистрировать проход: машина уже на территории, ждёт входных весов."""
    cargo_name = (cargo_name or "").strip()
    if not cargo_name:
        raise validation_error("Укажите, что вывозят", "cargo_required")

    # This is the first database lock. The monitor and all manual passage
    # mutations use State -> capture/event -> Wagon ordering.
    automation_state, automatic_capture = lock_automatic_passage_lane()
    assert_automatic_passage_lane_allows_manual_operation(
        automation_state,
        automatic_capture,
    )

    plate_event = None
    if vehicle_plate_event_id not in (None, ""):
        plate_event = _locked_vehicle_plate_event(vehicle_plate_event_id)
        number = plate_event.vehicle_number
        number_source = "camera"
        number_camera_source = plate_event.camera
    else:
        number = normalize_passage_number(number)
        number_source = "manual"
        number_camera_source = ""

    create_values = {
        "supply": None,
        "number": number,
        "direction": Wagon.PASSAGE,
        "workflow": "simple",
        "cargo_name": cargo_name,
        "status": st.ARRIVED,
        "arrived_at": timezone.now(),
        "arrived_by": user,
        "number_source": number_source,
        "number_camera_source": number_camera_source,
        "vehicle_plate_event": plate_event,
        "note": note or "",
    }
    try:
        with transaction.atomic():
            wagon = Wagon.objects.create(**create_values)
    except IntegrityError as exc:
        if (
            number
            and Wagon.objects.filter(
                direction=Wagon.PASSAGE,
                number=number,
                status__in=st.ON_SITE_STATUSES,
            ).exists()
        ):
            raise validation_error(
                f"Машина {number} уже находится на территории",
                "passage_already_on_site",
            ) from exc
        raise validation_error(
            "Событие номера машины уже использовано",
            "vehicle_plate_event_unavailable",
        ) from exc

    if plate_event is not None:
        _finish_auto_event(
            plate_event,
            status=VehiclePlateEvent.PROCESSED,
            action=AUTO_ACTION_MANUAL_ENTRY,
        )

    log_wagon_event(
        wagon,
        "passage",
        f"{wagon}: заезд за «{cargo_name}»",
        user,
        cargo_name=cargo_name,
        vehicle_plate_event_id=(
            str(plate_event.event_id) if plate_event is not None else None
        ),
        camera_source=number_camera_source,
    )
    fence_automatic_passage_lane_for_manual_mutation(automation_state)
    return wagon


@transaction.atomic
def record_passage_entry_weight(
    wagon: Wagon,
    weight_kg: int,
    user,
    *,
    occurred_at=None,
    **kwargs,
) -> Wagon:
    """Весы на въезде: машина пустая. Дальше её грузят."""
    if not wagon.is_passage:
        raise validation_error("Это приход, а не проход", "not_passage")
    ensure_transition(wagon, st.AT_SILO)
    wagon.gross_weight_kg = record_weighing(wagon, "gross", weight_kg, user, occurred_at=occurred_at, **kwargs)
    wagon.silo_arrived_at = occurred_at or timezone.now()
    wagon.unloading_started_at = wagon.silo_arrived_at
    wagon.save(
        update_fields=["gross_weight_kg", "silo_arrived_at", "unloading_started_at"]
    )
    set_status(
        wagon,
        st.AT_SILO,
        user,
        f"{wagon}: заезд {wagon.gross_weight_kg} кг, "
        f"загрузка «{wagon.cargo_name}»",
        entry_weight_kg=wagon.gross_weight_kg,
    )
    return wagon


@transaction.atomic
def record_passage_exit_weight(
    wagon: Wagon,
    weight_kg: int,
    user,
    *,
    occurred_at=None,
    **kwargs,
) -> Wagon:
    """Весы на выезде: машина гружёная. Нетто = выезд − заезд, цикл закрыт."""
    if not wagon.is_passage:
        raise validation_error("Это приход, а не проход", "not_passage")
    ensure_transition(wagon, st.TARE_WEIGHED)
    if wagon.gross_weight_kg is None:
        raise validation_error("Сначала зафиксируйте вес на въезде", "entry_weight_required")
    exit_weight = record_weighing(wagon, "tare", weight_kg, user, occurred_at=occurred_at, **kwargs)
    return finish_passage_exit(wagon, exit_weight, user, occurred_at=occurred_at)


def finish_passage_exit(
    wagon: Wagon,
    exit_weight: int,
    user,
    *,
    occurred_at=None,
) -> Wagon:
    """Close a passage whose loaded weight is already in the journal."""

    # Обратная приходу проверка: гружёная машина обязана быть тяжелее пустой.
    if exit_weight <= wagon.gross_weight_kg:
        raise validation_error(
            "Вес на выезде должен быть больше веса на въезде: машина уезжает гружёной",
            "bad_exit_weight",
        )
    wagon.tare_weight_kg = exit_weight
    wagon.net_weight_kg = wagon.computed_net_kg()
    wagon.unloading_finished_at = occurred_at or timezone.now()
    wagon.save(
        update_fields=[
            "tare_weight_kg",
            "net_weight_kg",
            "unloading_finished_at",
        ]
    )
    set_status(
        wagon,
        st.TARE_WEIGHED,
        user,
        f"{wagon}: выезд {exit_weight} кг, "
        f"вывезено {wagon.net_weight_kg} кг «{wagon.cargo_name}»",
        entry_weight_kg=wagon.gross_weight_kg,
        exit_weight_kg=exit_weight,
        net_weight_kg=wagon.net_weight_kg,
    )
    # Проход не оприходуется в силос: груз уезжает, остатки не трогаем.
    # Статусная цепочка та же, поэтому INVENTORIED проставляем явно.
    set_status(
        wagon,
        st.INVENTORIED,
        user,
        f"{wagon}: вывоз зафиксирован",
    )
    set_status(wagon, st.EXIT_ALLOWED, user, "Выезд разрешён")
    return register_exit(
        wagon,
        user,
        note="Выезд после загрузки",
        occurred_at=occurred_at,
    )


# ── Автоматический вывоз по событиям номера ─────────────────────────────────


@dataclass(frozen=True, slots=True)
class VehiclePlateAutomationResult:
    status: str
    action: str = ""
    error: str = ""
    retryable: bool = False
    wagon_id: int | None = None
    weight_kg: int | None = None
    unassigned_id: int | None = None


@dataclass(frozen=True, slots=True)
class _AutomationClaim:
    event_id: int
    action: str
    attempt: int


def _auto_processing_lease() -> timedelta:
    timeout = float(settings.TRUCK_SCALE_TIMEOUT_SECONDS)
    return timedelta(seconds=max(5.0, timeout + 2.0))


def _lock_auto_lane_mutex(event: VehiclePlateEvent) -> None:
    # Every event is committed before automation starts. The oldest durable row
    # on this physical lane is therefore a stable mutex shared by all plates
    # and retries. Two vehicles must never sample the same scale concurrently.
    (
        VehiclePlateEvent.objects.select_for_update()
        .filter(
            camera=event.camera,
            source=event.source,
        )
        .order_by("id")
        .first()
    )


def _event_wagon(event: VehiclePlateEvent, action: str) -> Wagon | None:
    field = (
        "exit_vehicle_plate_event_id"
        if action == AUTO_ACTION_EXIT
        else "vehicle_plate_event_id"
    )
    return Wagon.objects.filter(**{field: event.pk}).first()


def _terminal_automation_result(
    event: VehiclePlateEvent,
    *,
    already_processed: bool = True,
) -> VehiclePlateAutomationResult:
    if event.processing_status == VehiclePlateEvent.FAILED:
        return VehiclePlateAutomationResult(
            status="manual_required",
            action=event.processing_action,
            error=event.processing_error or "automation_failed",
        )
    if event.processing_action == AUTO_ACTION_IGNORED:
        return VehiclePlateAutomationResult(
            status="ignored",
            action=AUTO_ACTION_IGNORED,
            error=event.processing_error,
        )
    action = event.processing_action
    wagon = _event_wagon(event, action)
    weight = None
    if wagon is not None:
        weight = (
            wagon.exit_weight_kg
            if action == AUTO_ACTION_EXIT
            else wagon.entry_weight_kg
        )
    return VehiclePlateAutomationResult(
        status="already_processed" if already_processed else "processed",
        action=action,
        wagon_id=wagon.pk if wagon is not None else None,
        weight_kg=weight,
    )


def _finish_auto_event(
    event: VehiclePlateEvent,
    *,
    status: str,
    action: str,
    error: str = "",
    now=None,
) -> None:
    event.processing_status = status
    event.processing_action = action
    event.processing_error = error[:64]
    event.processing_started_at = None
    event.processed_at = now or timezone.now()
    event.save(
        update_fields=[
            "processing_status",
            "processing_attempts",
            "processing_action",
            "processing_error",
            "processing_started_at",
            "processed_at",
        ]
    )


def _fail_auto_event(
    event: VehiclePlateEvent,
    error: str,
    *,
    now=None,
    count_attempt: bool = False,
) -> VehiclePlateAutomationResult:
    """Terminally fail the event; the operator takes over this weighing."""

    if count_attempt:
        event.processing_attempts += 1
    _finish_auto_event(
        event,
        status=VehiclePlateEvent.FAILED,
        action=event.processing_action,
        error=error,
        now=now,
    )
    return _terminal_automation_result(event, already_processed=False)


def _relock_claimed_event(
    claim: _AutomationClaim,
) -> VehiclePlateEvent | VehiclePlateAutomationResult:
    """Re-lock the claimed event after scale I/O; a result means stop here."""

    hint = VehiclePlateEvent.objects.get(pk=claim.event_id)
    _lock_auto_lane_mutex(hint)
    event = VehiclePlateEvent.objects.select_for_update().get(pk=claim.event_id)
    if event.processing_status in (
        VehiclePlateEvent.PROCESSED,
        VehiclePlateEvent.FAILED,
    ):
        return _terminal_automation_result(event)
    if (
        event.processing_status != VehiclePlateEvent.PROCESSING
        or event.processing_action != claim.action
        or event.processing_attempts != claim.attempt
    ):
        return VehiclePlateAutomationResult(
            status="manual_required",
            action=event.processing_action,
            error="automation_state_changed",
        )
    return event


def _locked_auto_intent(
    event: VehiclePlateEvent,
    *,
    orientation: str = "",
) -> tuple[str | None, Wagon | None, str]:
    """Decide entry or exit for a recognized plate without an operator.

    The camera's front/rear verdict is the primary signal: a truck facing the
    scale camera is driving in, a truck showing its tail is driving out. The
    passage state then says which exact plate that concerns. A new entry
    requires a verdict; an exact existing plate can use the trip state.
    Conflicts are returned to the coordinator for operator review.
    """
    passages = list(
        Wagon.objects.select_for_update(of=("self",))
        .filter(
            direction=Wagon.PASSAGE,
            number=event.vehicle_number,
            status__in=st.ON_SITE_STATUSES,
        )
        .order_by("id")[:2]
    )
    if not passages:
        cooldown = timedelta(
            seconds=settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS
        )
        recent_completed = (
            Wagon.objects.select_for_update(of=("self",))
            .filter(
                direction=Wagon.PASSAGE,
                number=event.vehicle_number,
                status=st.COMPLETED,
                exited_at__isnull=False,
                exited_at__gte=event.detected_at - cooldown,
            )
            .order_by("-exited_at", "-id")
            .first()
        )
        if recent_completed is not None:
            return None, recent_completed, "recent_completed_passage"
        if orientation == VEHICLE_ORIENTATION_REAR:
            # Leaving loaded without an open trip: the entry was missed.
            return AUTO_ACTION_EXIT, None, ""
        if not orientation:
            return None, None, "orientation_unknown"
        return AUTO_ACTION_ENTRY, None, ""
    if len(passages) != 1:
        return None, None, "ambiguous_active_passage"

    wagon = passages[0]
    if (
        wagon.entry_weight_kg is not None
        and wagon.arrived_at
        and event.detected_at < wagon.arrived_at
    ):
        return None, wagon, "passage_time_conflict"
    if wagon.status == st.ARRIVED and wagon.entry_weight_kg is None:
        # A dispatcher pre-registered this plate; the truck now stands on the
        # scale for its empty weight.
        return AUTO_ACTION_ENTRY, wagon, ""
    valid_state = (
        wagon.status == st.AT_SILO
        and wagon.entry_weight_kg is not None
        and wagon.exit_weight_kg is None
        and wagon.arrived_at is not None
    )
    if not valid_state:
        return None, wagon, "passage_state_mismatch"
    if orientation == VEHICLE_ORIENTATION_FRONT:
        return None, wagon, "open_trip_conflict"
    minimum_exit_at = wagon.arrived_at + timedelta(
        seconds=settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS
    )
    if event.detected_at < minimum_exit_at:
        return None, wagon, "entry_exit_too_close"
    return AUTO_ACTION_EXIT, wagon, ""


def passage_entry_at(wagon: Wagon):
    return wagon.silo_arrived_at or wagon.arrived_at


def _park_weighing(
    reading: scale.ScaleReading,
    *,
    weight_kg: int,
    stable_weight_at,
    camera: str,
    photo_request_id,
    orientation: str,
    reason: str,
    message: str,
    user,
    vehicle_number: str = "",
    capture: AutomaticPassageCapture | None = None,
    **payload,
) -> UnassignedWeighing:
    """Сохранить вес без рейса: привязку к машине сделает оператор."""

    item = UnassignedWeighing.objects.create(
        capture=capture,
        weight_kg=weight_kg,
        stable_weight_at=stable_weight_at,
        scale_number=scale.TRUCK_SCALE_KEY,
        scale_age_seconds=reading.age_seconds,
        scale_updated_at=reading.updated_at or "",
        camera=camera,
        photo_request_id=photo_request_id,
        vehicle_number=vehicle_number,
        orientation=orientation,
        reason=reason,
    )
    log_event(
        "grain_unassigned_weighing",
        message,
        user=user,
        payload={
            "unassigned_id": item.pk,
            "weight_kg": weight_kg,
            "vehicle_number": vehicle_number,
            "camera_source": camera,
            "orientation": orientation,
            "reason": reason,
            "auto": True,
            **payload,
        },
    )
    return item


def _is_missed_entry_for(item: UnassignedWeighing, wagon: Wagon) -> bool:
    """A parked weight earlier and lighter than the booked entry is the real entry."""

    entry_at = passage_entry_at(wagon)
    if entry_at is None or item.stable_weight_at >= entry_at:
        return False
    if item.orientation == VEHICLE_ORIENTATION_REAR:
        return False
    return (
        item.orientation == VEHICLE_ORIENTATION_FRONT
        or item.weight_kg < (wagon.gross_weight_kg or 0)
    )


def _swap_missed_entry(
    item: UnassignedWeighing,
    wagon: Wagon,
    user,
    kwargs: dict,
) -> None:
    """The booked entry was really the loaded exit; the parked weight is the entry."""

    booked_exit = wagon.gross_weight_kg
    booked_exit_at = passage_entry_at(wagon)
    booked_record = (
        WeighingRecord.objects.filter(wagon=wagon, kind="gross").order_by("-id").first()
    )
    wagon.gross_weight_kg = record_weighing(wagon, "gross", item.weight_kg, user, occurred_at=item.stable_weight_at, **kwargs)
    wagon.silo_arrived_at = item.stable_weight_at
    wagon.unloading_started_at = item.stable_weight_at
    if wagon.arrived_at is None or wagon.arrived_at > item.stable_weight_at:
        wagon.arrived_at = item.stable_weight_at
    wagon.save(
        update_fields=[
            "gross_weight_kg",
            "silo_arrived_at",
            "unloading_started_at",
            "arrived_at",
        ]
    )
    if booked_record is not None:
        booked_record.kind = "tare"
        booked_record.previous_weight_kg = None
        booked_record.save(update_fields=["kind", "previous_weight_kg"])
    log_wagon_event(
        wagon,
        "unassigned_weighing",
        f"{wagon}: заезд был пропущен — "
        f"{item.weight_kg} кг записано как заезд, прежний вес {booked_exit} кг "
        "стал выездом",
        user,
        unassigned_id=item.pk,
        entry_weight_kg=item.weight_kg,
        exit_weight_kg=booked_exit,
    )
    finish_passage_exit(wagon, booked_exit, user, occurred_at=booked_exit_at)


@transaction.atomic
def _begin_vehicle_plate_automation(
    event_pk: int,
    *,
    now,
    orientation: str = "",
) -> VehiclePlateAutomationResult | _AutomationClaim:
    hint = VehiclePlateEvent.objects.get(pk=event_pk)
    _lock_auto_lane_mutex(hint)
    event = VehiclePlateEvent.objects.select_for_update().get(pk=event_pk)

    if event.processing_status in (
        VehiclePlateEvent.PROCESSED,
        VehiclePlateEvent.FAILED,
    ):
        return _terminal_automation_result(event)

    if (
        event.camera != settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA
        or event.source != settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE
    ):
        event.processing_attempts += 1
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.PROCESSED,
            action=AUTO_ACTION_IGNORED,
            error="wrong_lane",
            now=now,
        )
        return _terminal_automation_result(event, already_processed=False)

    lease_cutoff = now - _auto_processing_lease()
    if event.processing_status == VehiclePlateEvent.PROCESSING:
        if (
            event.processing_started_at is not None
            and event.processing_started_at >= lease_cutoff
        ):
            return VehiclePlateAutomationResult(
                status="retry",
                action=event.processing_action,
                error="automation_busy",
                retryable=True,
            )
        if event.processing_action in {AUTO_ACTION_ENTRY, AUTO_ACTION_EXIT}:
            # The automatic scale coordinator has already persisted the exact
            # physical sample.  Reclaiming a stale DB apply is therefore safe
            # and must not read the scale or ask Camera-PC to create a second
            # recognition request.
            event.processing_attempts += 1
            event.processing_started_at = now
            event.save(
                update_fields=[
                    "processing_attempts",
                    "processing_started_at",
                ]
            )
            return _AutomationClaim(
                event_id=event.pk,
                action=event.processing_action,
                attempt=event.processing_attempts,
            )
        return _fail_auto_event(event, "processing_interrupted", now=now)

    other_processing = (
        VehiclePlateEvent.objects.select_for_update()
        .filter(
            camera=event.camera,
            source=event.source,
            processing_status=VehiclePlateEvent.PROCESSING,
        )
        .exclude(pk=event.pk)
        .order_by("detected_at", "id")
        .first()
    )
    if other_processing is not None:
        if (
            other_processing.processing_started_at is not None
            and other_processing.processing_started_at < lease_cutoff
        ):
            _finish_auto_event(
                other_processing,
                status=VehiclePlateEvent.FAILED,
                action=other_processing.processing_action,
                error="processing_interrupted",
                now=now,
            )
        return _fail_auto_event(
            event, "lane_busy", now=now, count_attempt=True
        )

    action, _wagon, error = _locked_auto_intent(event, orientation=orientation)
    if error:
        return _fail_auto_event(event, error, now=now, count_attempt=True)

    event.processing_status = VehiclePlateEvent.PROCESSING
    event.processing_attempts += 1
    event.processing_action = action
    event.processing_error = ""
    event.processing_started_at = now
    event.processed_at = None
    event.save(
        update_fields=[
            "processing_status",
            "processing_attempts",
            "processing_action",
            "processing_error",
            "processing_started_at",
            "processed_at",
        ]
    )
    return _AutomationClaim(
        event_id=event.pk,
        action=action,
        attempt=event.processing_attempts,
    )


@transaction.atomic
def _apply_vehicle_plate_automation(
    claim: _AutomationClaim,
    *,
    reading: scale.ScaleReading,
    weight_kg: int,
    user,
    photo_request_id=None,
    photo_camera: str = "",
    orientation: str = "",
) -> VehiclePlateAutomationResult:
    scale.configure_authoritative_db_timeouts()
    event = _relock_claimed_event(claim)
    if isinstance(event, VehiclePlateAutomationResult):
        return event

    action, wagon, intent_error = _locked_auto_intent(event, orientation=orientation)
    if intent_error or action != claim.action:
        return _fail_auto_event(event, intent_error or "passage_state_changed")

    kwargs = {
        **_scale_kwargs(reading, scale.TRUCK_SCALE_KEY),
        "photo_request_id": photo_request_id,
        "photo_camera": photo_camera,
        "orientation": orientation,
    }
    if action == AUTO_ACTION_ENTRY:
        if wagon is None:
            try:
                with transaction.atomic():
                    wagon = Wagon.objects.create(
                        supply=None,
                        number=event.vehicle_number,
                        direction=Wagon.PASSAGE,
                        workflow="simple",
                        cargo_name=settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME,
                        status=st.ARRIVED,
                        arrived_at=event.detected_at,
                        arrived_by=user,
                        number_source="camera",
                        number_camera_source=event.camera,
                        vehicle_plate_event=event,
                        note="Автоматически оформлено по событию камеры",
                    )
            except IntegrityError:
                return _fail_auto_event(event, "ambiguous_active_passage")
            log_wagon_event(
                wagon,
                "passage",
                f"Вывоз {wagon.number}: автоматический заезд за «{wagon.cargo_name}»",
                user,
                cargo_name=wagon.cargo_name,
                vehicle_plate_event_id=str(event.event_id),
                camera_source=event.camera,
                auto=True,
            )
        else:
            # The dispatcher registered this plate ahead of time; bind the
            # camera event to that passage instead of creating a twin.
            try:
                with transaction.atomic():
                    wagon.vehicle_plate_event = event
                    wagon.number_camera_source = event.camera
                    wagon.save(
                        update_fields=["vehicle_plate_event", "number_camera_source"]
                    )
            except IntegrityError:
                return _fail_auto_event(event, "vehicle_plate_event_unavailable")
            log_wagon_event(
                wagon,
                "passage",
                f"Вывоз {wagon.number}: автоматический заезд по заранее "
                "зарегистрированному рейсу",
                user,
                vehicle_plate_event_id=str(event.event_id),
                camera_source=event.camera,
                auto=True,
            )
        record_passage_entry_weight(
            wagon,
            weight_kg,
            user,
            occurred_at=event.detected_at,
            **kwargs,
        )
    else:
        if wagon is None:
            # A loaded truck shows its tail but has no open trip under its
            # plate: the empty entry was missed or booked without a number.
            # Preserve this weight for the operator; timing and other blank
            # entries cannot establish which truck it belongs to.
            parked = _park_weighing(
                reading,
                weight_kg=weight_kg,
                stable_weight_at=event.detected_at,
                camera=event.camera,
                photo_request_id=photo_request_id,
                orientation=VEHICLE_ORIENTATION_REAR,
                reason="entry_missing",
                message=(
                    f"Выезд {event.vehicle_number} {weight_kg} кг без заезда: "
                    "рейс не найден, вес и фото сохранены для оператора"
                ),
                user=user,
                vehicle_number=event.vehicle_number,
            )
            _finish_auto_event(
                event,
                status=VehiclePlateEvent.PROCESSED,
                action=AUTO_ACTION_UNASSIGNED,
            )
            return VehiclePlateAutomationResult(
                status="processed",
                action=AUTO_ACTION_UNASSIGNED,
                weight_kg=weight_kg,
                unassigned_id=parked.pk,
            )
        if weight_kg <= (wagon.entry_weight_kg or 0):
            return _fail_auto_event(event, "exit_weight_not_greater")
        try:
            with transaction.atomic():
                wagon.exit_vehicle_plate_event = event
                wagon.save(update_fields=["exit_vehicle_plate_event"])
        except IntegrityError:
            return _fail_auto_event(event, "vehicle_plate_event_unavailable")
        record_passage_exit_weight(
            wagon,
            weight_kg,
            user,
            occurred_at=event.detected_at,
            **kwargs,
        )

    _finish_auto_event(
        event,
        status=VehiclePlateEvent.PROCESSED,
        action=action,
    )
    return VehiclePlateAutomationResult(
        status="processed",
        action=action,
        wagon_id=wagon.pk,
        weight_kg=weight_kg,
    )


def apply_automatic_passage_scale_sample(
    event_pk: int,
    *,
    reading: scale.ScaleReading,
    user=None,
    photo_request_id=None,
    photo_camera: str = "",
    orientation: str = "",
) -> VehiclePlateAutomationResult:
    """Apply a previously persisted automatic weight/OCR pair.

    This path deliberately performs no hardware I/O.  Its caller owns a
    durable scale sample and Camera-PC idempotency key, so an interrupted DB
    apply can be reclaimed after the normal lane lease without sampling a
    later vehicle.
    """

    claim_or_result = _begin_vehicle_plate_automation(
        event_pk,
        now=timezone.now(),
        orientation=orientation,
    )
    if isinstance(claim_or_result, VehiclePlateAutomationResult):
        return claim_or_result
    return _apply_vehicle_plate_automation(
        claim_or_result,
        reading=reading,
        weight_kg=whole_scale_weight_kg(reading),
        user=user,
        photo_request_id=photo_request_id,
        photo_camera=photo_camera,
        orientation=orientation,
    )


@transaction.atomic
def apply_unidentified_passage_scale_sample(
    *,
    reading: scale.ScaleReading,
    camera: str,
    request_id,
    stable_weight_at,
    capture: AutomaticPassageCapture | None = None,
    user=None,
    orientation: str = "",
) -> VehiclePlateAutomationResult:
    """Save a known front entry without a plate; park all uncertain pairings.

    Neither a single open trip nor a weight threshold identifies a truck.
    """

    weight_kg = whole_scale_weight_kg(reading)
    kwargs = {
        **_scale_kwargs(reading, scale.TRUCK_SCALE_KEY),
        "photo_request_id": request_id,
        "photo_camera": camera,
        "orientation": orientation,
    }
    open_passages = list(
        Wagon.objects.select_for_update(of=("self",))
        .filter(direction=Wagon.PASSAGE, status__in=st.ON_SITE_STATUSES)
        .order_by("id")
    )
    open_passage_ids = [wagon.pk for wagon in open_passages]
    if orientation == VEHICLE_ORIENTATION_FRONT:
        wagon = Wagon.objects.create(
            supply=None,
            number="",
            direction=Wagon.PASSAGE,
            workflow="simple",
            cargo_name=settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME,
            status=st.ARRIVED,
            arrived_at=stable_weight_at,
            arrived_by=user,
            number_source="camera",
            number_camera_source=camera,
            note="Автоматически оформлено по весам: номер не распознан, укажите его вручную",
        )
        log_wagon_event(
            wagon,
            "passage",
            f"Проход #{wagon.pk}: автоматический заезд, номер не распознан",
            user,
            cargo_name=wagon.cargo_name,
            camera_source=camera,
            auto=True,
            plate_unresolved=True,
            orientation=orientation,
        )
        record_passage_entry_weight(
            wagon,
            weight_kg,
            user,
            occurred_at=stable_weight_at,
            **kwargs,
        )
        return VehiclePlateAutomationResult(
            status="processed",
            action=AUTO_ACTION_ENTRY,
            wagon_id=wagon.pk,
            weight_kg=weight_kg,
        )

    reason = "plate_unreadable" if orientation else "orientation_unknown"
    if orientation == VEHICLE_ORIENTATION_REAR and not open_passages:
        reason = "entry_missing"

    item = _park_weighing(
        reading,
        weight_kg=weight_kg,
        stable_weight_at=stable_weight_at,
        camera=camera,
        photo_request_id=request_id,
        orientation=orientation,
        reason=reason,
        message=(
            f"Взвешивание {weight_kg} кг без номера: на территории "
            f"{len(open_passages)} маш., нужна привязка к рейсу"
        ),
        user=user,
        capture=capture,
        open_passage_ids=open_passage_ids,
    )
    return VehiclePlateAutomationResult(
        status="processed",
        action=AUTO_ACTION_UNASSIGNED,
        weight_kg=weight_kg,
        unassigned_id=item.pk,
    )


def unassigned_scale_kwargs(item: UnassignedWeighing) -> dict:
    return {
        "source": "scale",
        "scale_number": item.scale_number,
        "scale_age_seconds": item.scale_age_seconds,
        "scale_updated_at": item.scale_updated_at or None,
        "photo_request_id": item.photo_request_id,
        "photo_camera": item.camera,
        "orientation": item.orientation,
    }


def move_unassigned_photo(item: UnassignedWeighing, wagon: Wagon, kind: str) -> None:
    if not item.photo:
        return
    weighing = (
        WeighingRecord.objects.filter(wagon=wagon, kind=kind).order_by("-id").first()
    )
    if weighing is None or weighing.photo:
        return
    # Same storage, same file: only the reference moves.
    weighing.photo.name = item.photo.name
    weighing.save(update_fields=["photo"])


def resolve_unassigned_weighing(item: UnassignedWeighing, wagon: Wagon, action: str, user) -> None:
    """Close a parked weighing as the trip's entry or exit; its photo moves to that record."""
    move_unassigned_photo(item, wagon, "gross" if action == AUTO_ACTION_ENTRY else "tare")
    item.status, item.wagon, item.action = UnassignedWeighing.ASSIGNED, wagon, action
    item.resolved_by, item.resolved_at = user, timezone.now()
    item.save(update_fields=["status", "wagon", "action", "resolved_by", "resolved_at"])


def bind_passage_capture(item: UnassignedWeighing) -> None:
    """The camera capture of a booked weighing follows it to the trip and its action."""
    if item.capture_id:
        AutomaticPassageCapture.objects.filter(pk=item.capture_id).update(wagon_id=item.wagon_id, action=item.action)


def open_camera_passage(number: str, weighing: UnassignedWeighing, *, cargo_name=None) -> Wagon:
    """A trip the camera opened at a saved weighing; the weight is booked by the caller.

    Direct creation intentionally avoids the manual-action lane fence.
    """
    return Wagon.objects.create(
        direction=Wagon.PASSAGE, workflow="simple", number=number,
        status=st.ARRIVED, arrived_at=weighing.stable_weight_at,
        number_source="camera", number_camera_source=weighing.camera,
        cargo_name=settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME if cargo_name is None else cargo_name,
    )


@transaction.atomic
def assign_unassigned_weighing(
    item: UnassignedWeighing,
    wagon: Wagon,
    user,
) -> UnassignedWeighing:
    """Attach a parked weight to the passage the operator points at."""

    # Lane -> weighing -> visit: the lock order of automatic booking and of
    # the timer reconcile. Locking the weighing first could deadlock against
    # a reconcile that already holds the lane and comes for this weighing.
    lock_passage_lane()
    item = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if item.status != UnassignedWeighing.OPEN:
        raise validation_error("Это взвешивание уже обработано", "unassigned_weighing_resolved")
    wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon.pk)
    if not wagon.is_passage:
        raise validation_error("Привязать взвешивание можно только к вывозу", "not_passage")
    kwargs = unassigned_scale_kwargs(item)
    if wagon.status == st.ARRIVED and wagon.gross_weight_kg is None:
        if item.orientation == VEHICLE_ORIENTATION_REAR:
            raise validation_error("Камера видит выезд. Выберите существующий заезд или сохранённую тару.", "rear_cannot_be_entry")
        record_passage_entry_weight(
            wagon, item.weight_kg, user, occurred_at=item.stable_weight_at, **kwargs
        )
        action = AUTO_ACTION_ENTRY
    elif (
        wagon.status == st.AT_SILO
        and wagon.tare_weight_kg is None
        and _is_missed_entry_for(item, wagon)
    ):
        _swap_missed_entry(item, wagon, user, kwargs)
        action = AUTO_ACTION_ENTRY
    elif wagon.status == st.AT_SILO and wagon.tare_weight_kg is None:
        record_passage_exit_weight(
            wagon, item.weight_kg, user, occurred_at=item.stable_weight_at, **kwargs
        )
        action = AUTO_ACTION_EXIT
    else:
        raise validation_error("Этот рейс сейчас не ждёт взвешивания", "wagon_not_awaiting_weight")
    resolve_unassigned_weighing(item, wagon, action, user)
    log_wagon_event(
        wagon,
        "unassigned_weighing",
        f"{wagon}: привязано взвешивание "
        f"{item.weight_kg} кг ({'заезд' if action == AUTO_ACTION_ENTRY else 'выезд'})",
        user,
        unassigned_id=item.pk,
        action=action,
        weight_kg=item.weight_kg,
    )
    return item


@transaction.atomic
def create_passage_from_unassigned_weighing(
    item: UnassignedWeighing,
    user,
    *,
    number="",
    cargo_name="",
) -> UnassignedWeighing:
    """Open a new passage for a parked weight and record it as the entry."""

    # Keep the lane -> event -> wagon lock order shared with automatic booking
    # and manual missing-entry recovery. Locking the item first could deadlock
    # while create_passage waited for another writer that already owned the lane.
    lock_passage_lane_for_manual_operation()
    locked = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if locked.status != UnassignedWeighing.OPEN:
        raise validation_error("Это взвешивание уже обработано", "unassigned_weighing_resolved")
    if locked.orientation == VEHICLE_ORIENTATION_REAR:
        raise validation_error("Камера видит выезд. Выберите существующий заезд или сохранённую тару.", "rear_cannot_be_entry")
    wagon = create_passage(
        user,
        number=number or locked.vehicle_number,
        cargo_name=cargo_name or settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME,
    )
    return assign_unassigned_weighing(locked, wagon, user)


@transaction.atomic
def discard_unassigned_weighing(
    item: UnassignedWeighing,
    user,
    *,
    reason="",
) -> UnassignedWeighing:
    item = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if item.status != UnassignedWeighing.OPEN:
        raise validation_error("Это взвешивание уже обработано", "unassigned_weighing_resolved")
    item.status = UnassignedWeighing.DISCARDED
    item.resolved_by = user
    item.resolved_at = timezone.now()
    item.save(update_fields=["status", "resolved_by", "resolved_at"])
    log_event(
        "grain_unassigned_weighing_discarded",
        f"Неопознанное взвешивание {item.weight_kg} кг отклонено",
        user=user,
        payload={
            "unassigned_id": item.pk,
            "weight_kg": item.weight_kg,
            "reason": str(reason or "")[:200],
        },
    )
    return item


@transaction.atomic
def set_passage_number(wagon: Wagon, raw_number, user, *, number_source="manual") -> Wagon:
    """Fill in or correct the plate of a passage the camera could not read.

    ``number_source`` names who supplied the plate: a person by default, the
    camera when automatic routing corrects a misread visit.
    """

    # Tare reuse validates the source plate under this same lane mutex.
    # Renaming a source between that validation and booking would otherwise
    # associate its tare with the previous vehicle number.
    lock_passage_lane()
    wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon.pk)
    if not wagon.is_passage:
        raise validation_error("Номер можно менять только у вывоза", "not_passage")
    if wagon.status in st.TERMINAL_STATUSES:
        raise validation_error("Рейс уже завершён", "wagon_finished")
    number = normalize_passage_number(raw_number)
    if not number:
        raise validation_error("Укажите номер машины", "number_required")
    if len(number) > 30:
        raise validation_error("Номер слишком длинный", "bad_number")
    previous = wagon.number
    if previous == number:
        return wagon
    wagon.number = number
    wagon.number_source = number_source
    try:
        with transaction.atomic():
            wagon.save(update_fields=["number", "number_source"])
    except IntegrityError as exc:
        raise validation_error(
            f"Машина {number} уже находится на территории",
            "passage_already_on_site",
        ) from exc
    from .historical_tare import confirmed_sources, remember
    from .models import VehicleTareMemory
    VehicleTareMemory.objects.filter(record__wagon=wagon).exclude(number=number).delete()
    confirmed_entry = confirmed_sources(number).filter(wagon=wagon).first()
    if confirmed_entry is not None:
        remember(confirmed_entry, number)
    log_wagon_event(
        wagon,
        "number",
        f"Проход #{wagon.pk}: номер «{previous or '—'}» → «{number}»",
        user,
        previous_number=previous,
        number=number,
    )
    return wagon


# ── Удаление рейса ─────────────────────────────────────────────────────────


DELETE_REASON_MIN_LENGTH = 5
DELETE_REASON_MAX_LENGTH = 200
UNRECORDED_GRAIN_CONFIRMATION_STATUSES = {st.UNLOADING, st.UNLOADING_COMPLETED}


def _normalized_delete_reason(reason) -> str:
    if not isinstance(reason, str):
        raise validation_error("Причина удаления должна быть строкой", "bad_delete_reason")
    normalized = " ".join(reason.split())
    if normalized and len(normalized) > DELETE_REASON_MAX_LENGTH:
        raise validation_error(
            f"Причина удаления не должна превышать {DELETE_REASON_MAX_LENGTH} символов",
            "delete_reason_too_long",
        )
    return normalized


@transaction.atomic
def delete_wagon(
    wagon: Wagon,
    user,
    reason: str = "",
    *,
    confirm_unrecorded_grain_handled=False,
) -> dict:
    """Удалить допустимый рейс, не искажая остатки и резервы.

    Сильное право ``grain.delete`` позволяет удалить завершённый рейс либо
    ошибочную запись, пока транспорт числится на территории. Для активной
    записи обязательна причина. Ожидаемые и ещё не зарегистрированные рейсы
    удаляются через управление поставкой, а не через этот аварийный контракт.

    Оприходованное зерно не исчезает молча: на каждое движение прихода
    пишется компенсирующий расход, поэтому остаток силоса сходится с
    журналом и после удаления. Сам леджер неизменяем — старые записи
    остаются, у них лишь отвязывается удаляемый вагон (FK стоит PROTECT).

    Проход силоса не касается, откатывать там нечего.
    """
    automation_state = None
    if wagon.is_passage:
        # This singleton is the lane mutex shared with the outbox importer.
        # It must be locked before Wagon, otherwise
        # State -> capture -> Wagon can deadlock with Wagon -> capture.
        # Any PROCESSING automatic capture freezes deletion: OCR may not have
        # produced a plate yet, and a concurrent exit could be reinterpreted as
        # a new entry after this wagon vanished.
        automation_state, automatic_lane_capture = lock_automatic_passage_lane()
        assert_automatic_passage_lane_allows_manual_operation(
            automation_state,
            automatic_lane_capture,
        )
    try:
        wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon.pk)
    except Wagon.DoesNotExist as exc:
        raise NotFound(
            {"detail": "Рейс уже удалён", "code": "wagon_not_found"}
        ) from exc
    processing_capture = (
        PassageWeightCapture.objects.select_for_update()
        .filter(wagon=wagon, status=PassageWeightCapture.PROCESSING)
        .only("pk")
        .first()
    )
    if processing_capture is not None:
        raise validation_error(
            "Рейс нельзя удалить, пока фиксируются вес и номер машины.",
            "passage_capture_in_progress",
        )
    supply = (
        GrainSupply.objects.select_for_update().get(pk=wagon.supply_id)
        if wagon.supply_id is not None
        else None
    )
    reason = _normalized_delete_reason(reason)
    if WeighingRecord.objects.filter(reference_record__wagon=wagon).exists():
        raise validation_error("Тара этого рейса используется в другом вывозе. Исходное взвешивание нужно сохранить для аудита.", "tare_reference_in_use")
    active_deletion = wagon.status in st.ON_SITE_STATUSES
    finished = wagon.status in st.FINISHED_STATUSES
    if not finished and not active_deletion:
        raise validation_error(
            "Рейс нельзя удалить на текущем этапе. "
            "Сначала завершите текущую физическую операцию.",
            "wagon_delete_not_allowed",
        )
    if active_deletion and len(reason) < DELETE_REASON_MIN_LENGTH:
        raise validation_error(
            f"Укажите причину удаления (минимум {DELETE_REASON_MIN_LENGTH} символов)",
            "delete_reason_required",
        )
    needs_unrecorded_grain_confirmation = (
        active_deletion
        and wagon.direction == Wagon.INTAKE
        and wagon.status in UNRECORDED_GRAIN_CONFIRMATION_STATUSES
    )
    if (
        needs_unrecorded_grain_confirmation
        and confirm_unrecorded_grain_handled is not True
    ):
        raise validation_error(
            "Подтвердите, что физически разгруженное зерно уже учтено "
            "отдельно либо фактической разгрузки не было",
            "unrecorded_grain_confirmation_required",
        )

    reservation = (
        SiloReservation.objects.filter(wagon=wagon)
        .values("id", "silo_id", "amount_kg", "active")
        .first()
    )
    income_movements = list(
        wagon.movements.filter(movement_type="income")
        .select_related("silo")
        .order_by("id")
    )
    snapshot = {
        "wagon_id": wagon.pk,
        "supply_id": wagon.supply_id,
        "number": wagon.number,
        "direction": wagon.direction,
        "workflow": wagon.workflow,
        "status": wagon.status,
        "gross_weight_kg": wagon.gross_weight_kg,
        "tare_weight_kg": wagon.tare_weight_kg,
        "net_weight_kg": wagon.net_weight_kg,
        "assigned_silo_id": wagon.assigned_silo_id,
        "unloading_started_at": (
            wagon.unloading_started_at.isoformat()
            if wagon.unloading_started_at
            else None
        ),
        "unloading_finished_at": (
            wagon.unloading_finished_at.isoformat()
            if wagon.unloading_finished_at
            else None
        ),
        "weighing_count": wagon.weighings.count(),
        "lab_check_count": wagon.lab_checks.count(),
        "allocation_count": wagon.allocations.count(),
        "income_movement_ids": [movement.pk for movement in income_movements],
        "reservation": reservation,
    }
    reverted_kg = 0
    for movement in income_movements:
        adjust_silo(
            movement.silo,
            -movement.delta_kg,
            "expense",
            note=(f"Откат прихода рейса {wagon.label}" + (f": {reason}" if reason else "")),
            user=user,
            supply=movement.supply,
            batch_number=f"DELETE-WAGON-{wagon.pk}",
        )
        reverted_kg += movement.delta_kg

    # Леджер переживает удалённый рейс: обнуляем ссылку, а не запись.
    wagon.movements.update(wagon=None)
    released_reservation_kg = (
        reservation["amount_kg"] if reservation and reservation["active"] else 0
    )
    SiloReservation.objects.filter(wagon=wagon).delete()
    WeighingRecord.objects.filter(wagon=wagon).delete()
    LabCheck.objects.filter(wagon=wagon).delete()
    SiloAllocation.objects.filter(wagon=wagon).delete()

    log_event(
        "grain_wagon_deleted",
        f"Рейс {wagon.label} удалён"
        + (f", возвращено из силоса {reverted_kg} кг" if reverted_kg else ""),
        user=user,
        payload={
            **snapshot,
            "reverted_kg": reverted_kg,
            "released_reservation_kg": released_reservation_kg,
            "active_deletion": active_deletion,
            "unrecorded_grain_confirmation_required": (
                needs_unrecorded_grain_confirmation
            ),
            "confirm_unrecorded_grain_handled": (
                confirm_unrecorded_grain_handled is True
            ),
            "reason": reason,
        },
    )
    wagon.delete()
    # Fence an occupied snapshot obtained just before this transaction. Fresh
    # confirmed clear readings are required before the monitor may trigger.
    fence_automatic_passage_lane_for_manual_mutation(automation_state)
    # Поставка без вагонов больше ничего не ждёт.
    if supply and not supply.wagons.exists():
        supply.status = "closed"
        supply.save(update_fields=["status"])
    return {
        "reverted_kg": reverted_kg,
        "released_reservation_kg": released_reservation_kg,
    }


# ── Автоматический приход по камере ────────────────────────────────────────
# Датчика прибытия поезда на территории нет. Его роль играет детектор таблички
# вагона: табличка в кадре означает, что состав встал под разгрузку.
#
# Модель находит табличку, а номер используется только при подтверждённом OCR.
# Если OCR не уверен, рейс безопасно остаётся без номера и его допишет оператор.

# Пауза без детекций, после которой следующая табличка считается новым
# составом. Пока табличка видна раз за разом — это один и тот же поезд,
# и второй рейс на него заводить нельзя.
AUTO_ARRIVAL_GAP = timedelta(minutes=15)


def _open_camera_wagon() -> Wagon | None:
    """Незакрытый приход, заведённый камерой. Их не может быть двух сразу."""
    return (
        Wagon.objects.filter(
            direction=Wagon.INTAKE,
            number_source="camera",
            number="",
            status__in=st.ON_SITE_STATUSES,
        )
        .order_by("-id")
        .first()
    )


@transaction.atomic
def register_detected_arrival(
    user=None,
    *,
    camera_source: str = "",
    number: str = "",
) -> Wagon | None:
    """Открыть приход по табличке вагона. Повторную детекцию игнорирует.

    ``number`` — номер, которому OCR доверился сам (``accepted``). По нему
    приезд связывается с ожидаемой поставкой: диспетчер завёл её заранее, и
    камера должна занять готовый рейс, а не плодить рядом безымянный дубль.
    Нераспознанный номер оставляет рейс пустым — его допишет оператор.

    Возвращает рейс либо ``None``, если открывать нечего: состав уже на
    территории или его табличка была видна только что.
    """
    number = (number or "").strip()

    if number:
        # Номер известен: занимаем ожидаемый рейс, если он заведён заранее.
        expected = (
            Wagon.objects.select_for_update()
            .filter(number=number, status=st.EXPECTED)
            .order_by("id")
            .first()
        )
        if expected is not None:
            return arrive_expected_wagon(expected, user, camera_source)
        if Wagon.objects.filter(
            number=number,
            status__in=st.ON_SITE_STATUSES,
        ).exists():
            # Этот вагон уже на территории — повторная детекция его таблички.
            return None

    if _open_camera_wagon() is not None:
        return None

    recent = (
        Wagon.objects.filter(
            direction=Wagon.INTAKE,
            number_source="camera",
            arrived_at__gte=timezone.now() - AUTO_ARRIVAL_GAP,
        )
        .order_by("-arrived_at")
        .first()
    )
    if recent is not None:
        # Тот же состав всё ещё под камерой — новый рейс это не значит.
        return None

    wagon = Wagon.objects.create(
        supply=None,
        number=number,
        direction=Wagon.INTAKE,
        workflow="simple",
        status=st.ARRIVED,
        arrived_at=timezone.now(),
        arrived_by=user,
        number_source="camera",
        number_camera_source=camera_source or "",
    )
    log_wagon_event(
        wagon,
        "arrival",
        f"Камера зафиксировала прибытие состава (рейс #{wagon.pk})"
        + (
            f": вагон {number}"
            if number
            else ". Номер не распознан — укажите его вручную."
        ),
        user,
        camera_source=camera_source,
        number=number,
        auto=True,
    )
    return wagon


def arrive_expected_wagon(wagon: Wagon, user, camera_source: str) -> Wagon:
    """Ожидаемый рейс встал на территорию: заказ и поставка уже привязаны."""
    ensure_transition(wagon, st.ARRIVED)
    wagon.arrived_at = timezone.now()
    wagon.arrived_by = user
    wagon.number_source = "camera"
    wagon.number_camera_source = camera_source or ""
    wagon.save(
        update_fields=[
            "arrived_at",
            "arrived_by",
            "number_source",
            "number_camera_source",
        ]
    )
    set_status(
        wagon,
        st.ARRIVED,
        user,
        f"Камера распознала вагон {wagon.number}: прибытие по ожидаемому приходу",
        camera_source=camera_source,
        auto=True,
    )
    return wagon
