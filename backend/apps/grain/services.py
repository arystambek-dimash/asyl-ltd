"""Бизнес-операции прихода зерна. Статусы меняются только здесь.

Каждая операция атомарна, силос блокируется ``select_for_update`` перед
резервом и оприходованием — два вагона не займут одно и то же место.
"""

import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, InterfaceError, OperationalError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.cameras.models import VehiclePlateEvent
from apps.eventlog.services import log_event

from . import scale
from . import statuses as st
from .models import (
    AutomaticPassageCapture,
    GrainMovement,
    GrainSettings,
    GrainSupply,
    LabCheck,
    PassageScaleAutomationState,
    PassageWeightCapture,
    Silo,
    SiloAllocation,
    SiloReservation,
    SiloType,
    UnassignedWeighing,
    Wagon,
    WeighingRecord,
)

VEHICLE_PLATE_CAMERA = "cam1"
VEHICLE_PLATE_SOURCE = "main"
VEHICLE_PLATE_MAX_AGE = timedelta(minutes=5)
VEHICLE_PLATE_MAX_FUTURE = timedelta(minutes=1)
VEHICLE_PLATE_CANDIDATE_LIMIT = 5
VEHICLE_PLATE_AUTO_MAX_FUTURE = timedelta(seconds=5)
KZ_VEHICLE_PLATE_RE = re.compile(r"^[0-9]{3}[A-Z]{3}[0-9]{2}$")

AUTO_ACTION_ENTRY = "entry"
AUTO_ACTION_EXIT = "exit"
AUTO_ACTION_IGNORED = "ignored"
AUTO_ACTION_MANUAL_ENTRY = "manual_entry"
AUTO_ACTION_UNASSIGNED = "unassigned"


def _error(detail: str, code: str) -> ValidationError:
    return ValidationError({"detail": detail, "code": code})


def _log(wagon: Wagon, event: str, message: str, user, **payload):
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
    if not st.can_transition(wagon.status, target):
        current = st.WAGON_STATUS_LABELS.get(wagon.status, wagon.status)
        wanted = st.WAGON_STATUS_LABELS.get(target, target)
        raise _error(
            f"Переход «{current} → {wanted}» недопустим",
            "invalid_wagon_transition",
        )


def _set_status(wagon: Wagon, target: str, user, message: str, **payload):
    ensure_transition(wagon, target)
    old = wagon.status
    wagon.status = target
    wagon.save(update_fields=["status"])
    _log(wagon, "status", message, user, old_status=old, new_status=target, **payload)


# ── Поставки ────────────────────────────────────────────────────────────────


def publish_supply(supply: GrainSupply, user) -> GrainSupply:
    """DRAFT → EXPECTED: поставка попадает в список ожидаемых."""
    if supply.status != "draft":
        raise _error("Опубликовать можно только черновик", "supply_not_draft")
    supply.status = "expected"
    supply.save(update_fields=["status"])
    for wagon in supply.wagons.filter(status=st.EXPECTED):
        _log(wagon, "supply", f"Поставка #{supply.pk} опубликована", user)
    log_event(
        "grain_supply",
        f"Поставка #{supply.pk} от «{supply.supplier}» ожидается",
        user=user,
        payload={"supply_id": supply.pk, "action": "published"},
    )
    return supply


@transaction.atomic
def prepare_simple_supply(supply: GrainSupply, user) -> GrainSupply:
    """Создать новый короткий приход с одним поездом и заранее заданным силосом."""
    if not supply.grain_type_id:
        raise _error("Выберите тип зерна", "grain_type_required")
    if not supply.assigned_silo_id:
        raise _error("Выберите силос назначения", "silo_required")
    expected = int(supply.expected_total_kg or 0)
    if expected <= 0:
        raise _error("Укажите ожидаемый вес", "expected_weight_required")

    silo = Silo.objects.select_for_update().get(pk=supply.assigned_silo_id)
    if silo.status != "active":
        raise _error("Выбранный силос недоступен", "silo_inactive")
    if silo.silo_type_id and silo.silo_type_id != supply.grain_type_id:
        raise _error(
            "Тип зерна не совпадает с типом выбранного силоса",
            "grain_type_silo_mismatch",
        )
    if silo.free_capacity_kg < expected:
        raise _error(
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
    _log(
        wagon,
        "supply",
        f"Создан приход #{supply.pk}: ожидается поезд в силос «{silo.name}»",
        user,
        grain_type_id=supply.grain_type_id,
        silo_id=silo.pk,
        expected_weight_kg=expected,
    )
    return supply


def add_wagon_numbers(supply: GrainSupply, numbers: list[str], user) -> list[Wagon]:
    """Дозаполнение номеров: вагоны можно заявить и без номеров заранее."""
    created = []
    for raw in numbers:
        number = (raw or "").strip()
        if not number:
            continue
        if Wagon.objects.filter(
            number=number,
            status__in=st.ON_SITE_STATUSES | {st.EXPECTED},
        ).exists():
            raise _error(
                f"Вагон {number} уже заявлен или на территории",
                "wagon_number_busy",
            )
        wagon = Wagon.objects.create(supply=supply, number=number)
        _log(wagon, "supply", f"Вагон {number} добавлен в поставку #{supply.pk}", user)
        created.append(wagon)
    return created


# ── Прибытие ────────────────────────────────────────────────────────────────


@transaction.atomic
def register_arrival(
    number: str,
    user,
    supply: GrainSupply | None = None,
    *,
    number_source: str = "manual",
    camera_source: str = "",
) -> Wagon:
    number = (number or "").strip()
    if not number:
        raise _error("Укажите номер вагона", "wagon_number_required")
    if Wagon.objects.filter(
        number=number,
        status__in=st.ON_SITE_STATUSES,
    ).exists():
        raise _error(
            f"Вагон {number} уже зарегистрирован на территории",
            "wagon_already_on_site",
        )

    wagon = (
        Wagon.objects.select_for_update()
        .filter(number=number, status=st.EXPECTED)
        .order_by("id")
        .first()
    )
    if wagon is None and supply is not None:
        # В коротком флоу поезд создаётся заранее без номера: камера заполняет
        # его при фактическом въезде, не создавая дубликат поставки.
        wagon = (
            supply.wagons.select_for_update()
            .filter(number="", status=st.EXPECTED)
            .order_by("id")
            .first()
        )
        if wagon is None:
            wagon = Wagon.objects.create(
                supply=supply,
                number=number,
                status=st.EXPECTED,
                number_source=number_source,
                number_camera_source=camera_source,
            )
        else:
            wagon.number = number
            wagon.number_source = number_source
            wagon.number_camera_source = camera_source
            wagon.save(
                update_fields=["number", "number_source", "number_camera_source"]
            )
        _log(
            wagon,
            "arrival",
            f"Номер {number} привязан к приходу #{supply.pk}",
            user,
            number_source=number_source,
            camera_source=camera_source,
        )
    if wagon is None:
        # Незапланированное прибытие: до решения диспетчера на разгрузку нельзя.
        wagon = Wagon.objects.create(number=number, status=st.UNPLANNED, unplanned=True)
        _set_status(
            wagon,
            st.WAITING_FOR_APPROVAL,
            user,
            f"Незапланированный вагон {number} ждёт подтверждения",
        )
        return wagon

    wagon.number_source = number_source
    wagon.number_camera_source = camera_source
    wagon.arrived_at = timezone.now()
    wagon.arrived_by = user
    wagon.save(
        update_fields=[
            "number_source",
            "number_camera_source",
            "arrived_at",
            "arrived_by",
        ]
    )
    _set_status(wagon, st.ARRIVED, user, f"Вагон {number} прибыл")
    return wagon


@transaction.atomic
def approve_unplanned(wagon: Wagon, user, supply: GrainSupply | None = None) -> Wagon:
    if wagon.status != st.WAITING_FOR_APPROVAL:
        raise _error("Вагон не ждёт подтверждения", "wagon_not_waiting")
    if supply is not None:
        wagon.supply = supply
    wagon.arrived_at = timezone.now()
    wagon.arrived_by = user
    wagon.save(update_fields=["supply", "arrived_at", "arrived_by"])
    _set_status(
        wagon,
        st.ARRIVED,
        user,
        f"Незапланированный вагон {wagon.number} подтверждён диспетчером",
    )
    return wagon


# ── Взвешивания ─────────────────────────────────────────────────────────────


def _record_weighing(
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
):
    try:
        weight_kg = int(weight_kg)
    except (TypeError, ValueError):
        raise _error("Вес должен быть целым числом килограммов", "bad_weight")
    if weight_kg <= 0:
        raise _error("Вес должен быть положительным", "bad_weight")
    if source == "manual" and not manual_reason:
        raise _error("Для ручного ввода веса укажите причину", "manual_reason_required")
    previous = wagon.gross_weight_kg if kind == "gross" else wagon.tare_weight_kg
    WeighingRecord.objects.create(
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
    )
    scale_payload = {}
    if scale_age_seconds is not None:
        scale_payload["scale_age_seconds"] = str(scale_age_seconds)
    if scale_updated_at is not None:
        scale_payload["scale_updated_at"] = scale_updated_at
    _log(
        wagon,
        "weighing",
        f"Вагон {wagon.number}: {'брутто' if kind == 'gross' else 'тара'} "
        f"{weight_kg} кг",
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


def _whole_scale_weight_kg(reading: scale.ScaleReading) -> int:
    """Round a strict Decimal scale sample to the grain model's whole kg."""
    weight = reading.weight_kg.to_integral_value(rounding=ROUND_HALF_UP)
    if weight <= 0:
        raise scale.TruckScaleNotReady(
            "Показание весов после округления должно быть не меньше 1 кг."
        )
    return int(weight)


def _ensure_scale_action_ready(wagon: Wagon, action: str) -> None:
    """Reject stale or mismatched commands before contacting the scale."""
    targets = {
        "gross": st.GROSS_WEIGHED,
        "tare": st.TARE_WEIGHED,
        "entry": st.AT_SILO,
        "exit": st.TARE_WEIGHED,
    }
    try:
        target = targets[action]
    except KeyError as exc:
        raise ValueError(f"Unknown grain scale action: {action}") from exc

    simple_action = action in {"entry", "exit"}
    if simple_action != (wagon.workflow == "simple"):
        raise _error(
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
    _ensure_scale_action_ready(wagon, action)
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
        raise _error(
            "Состояние вагона изменилось во время чтения весов — повторите взвешивание",
            "wagon_changed_during_scale_read",
        )
    _ensure_scale_action_ready(wagon, action)
    expected_scale_key = (
        scale.TRUCK_SCALE_KEY if wagon.is_passage else scale.WAGON_SCALE_KEY
    )
    if scale_key != expected_scale_key:
        raise _error(
            "Маршрут рейса изменился во время чтения весов — повторите взвешивание",
            "wagon_changed_during_scale_read",
        )
    kwargs = {
        "source": "scale",
        "scale_number": scale_key,
        "scale_age_seconds": reading.age_seconds,
        "scale_updated_at": reading.updated_at,
    }
    weight_kg = _whole_scale_weight_kg(reading)
    if action == "gross":
        return record_gross(wagon, weight_kg, user, **kwargs)
    if action == "tare":
        return record_tare(wagon, weight_kg, user, **kwargs)
    if action == "entry":
        record = (
            record_passage_entry_weight
            if wagon.is_passage
            else record_simple_entry_weight
        )
        return record(wagon, weight_kg, user, **kwargs)
    if action == "exit":
        record = (
            record_passage_exit_weight
            if wagon.is_passage
            else record_simple_exit_weight
        )
        return record(wagon, weight_kg, user, **kwargs)
    raise ValueError(f"Unknown grain scale action: {action}")


@transaction.atomic
def record_gross(wagon: Wagon, weight_kg: int, user, **kwargs) -> Wagon:
    ensure_transition(wagon, st.GROSS_WEIGHED)
    wagon.gross_weight_kg = _record_weighing(wagon, "gross", weight_kg, user, **kwargs)
    wagon.save(update_fields=["gross_weight_kg"])
    _set_status(
        wagon, st.GROSS_WEIGHED, user, f"Вагон {wagon.number}: брутто зафиксировано"
    )
    # Лаборатория обязательна для каждого вагона — очередь встаёт сразу.
    _set_status(wagon, st.LAB_PENDING, user, f"Вагон {wagon.number} ждёт лабораторию")
    return wagon


@transaction.atomic
def record_simple_entry_weight(wagon: Wagon, weight_kg: int, user, **kwargs) -> Wagon:
    """Входные весы → сразу маршрут к заранее назначенному силосу."""
    if wagon.workflow != "simple":
        raise _error("Для вагона используется старый маршрут", "not_simple_flow")
    ensure_transition(wagon, st.AT_SILO)
    if not wagon.assigned_silo_id:
        raise _error("Для прихода не назначен силос", "silo_required")
    wagon.gross_weight_kg = _record_weighing(wagon, "gross", weight_kg, user, **kwargs)
    wagon.silo_arrived_at = timezone.now()
    wagon.unloading_started_at = wagon.silo_arrived_at
    wagon.save(
        update_fields=["gross_weight_kg", "silo_arrived_at", "unloading_started_at"]
    )
    _set_status(
        wagon,
        st.AT_SILO,
        user,
        f"Поезд {wagon.number} взвешен и направлен в силос "
        f"«{wagon.assigned_silo.name}»",
        gross_weight_kg=wagon.gross_weight_kg,
        silo_id=wagon.assigned_silo_id,
    )
    return wagon


# ── Лаборатория ─────────────────────────────────────────────────────────────

DECISION_STATUS = {
    "accepted": st.UNLOADING_ALLOWED,
    "accepted_with_restrictions": st.UNLOADING_ALLOWED,
    "rejected": st.REJECTED,
    "quarantine": st.QUARANTINE,
}


@transaction.atomic
def record_lab_check(wagon: Wagon, decision: str, user, **fields) -> LabCheck:
    if decision not in DECISION_STATUS:
        raise _error("Неизвестное решение лаборатории", "bad_lab_decision")
    target = DECISION_STATUS[decision]
    ensure_transition(wagon, target)
    check = LabCheck.objects.create(
        wagon=wagon, decision=decision, checked_by=user, **fields
    )
    _set_status(
        wagon,
        target,
        user,
        f"Лаборатория: вагон {wagon.number} — {decision}",
        decision=decision,
    )
    return check


# ── Силосы: подбор и резерв ────────────────────────────────────────────────


def suggest_silos(wagon: Wagon):
    """Подходящие силосы; настроенный маршрут прихода идёт первым."""
    culture = wagon.supply.culture if wagon.supply else ""
    grain_class = wagon.supply.grain_class if wagon.supply else ""
    need = wagon.planned_weight_kg or 0
    silos = Silo.objects.filter(status="active").select_related("silo_type")
    if wagon.status == st.QUARANTINE:
        silos = silos.filter(is_quarantine=True)
    else:
        silos = silos.filter(is_quarantine=False)
    suitable = []
    for silo in silos:
        if silo.grain_culture and culture and silo.grain_culture != culture:
            continue
        if (
            silo.grain_class
            and grain_class
            and silo.grain_class != grain_class
            and not silo.allow_mixing
        ):
            continue
        if silo.free_capacity_kg < need:
            continue
        suitable.append(silo)
    default_ids = set(
        SiloType.objects.filter(
            grain_culture=culture,
            grain_class=grain_class,
            default_silo__isnull=False,
        ).values_list("default_silo_id", flat=True)
    )
    return sorted(
        suitable,
        key=lambda silo: (
            silo.pk not in default_ids,
            -silo.free_capacity_kg,
            silo.name,
        ),
    )


def assign_silo(
    wagon: Wagon, silo: Silo, user, expected_kg: int | None = None
) -> Wagon:
    target = st.SILO_ASSIGNED
    ensure_transition(wagon, target)
    # Резерв: явный ввод → вес по документам/ожиданиям → брутто (нетто всегда
    # меньше брутто, так что бронь по брутто безопасна).
    amount = int(expected_kg or wagon.planned_weight_kg or wagon.gross_weight_kg or 0)
    if amount <= 0:
        raise _error(
            "Укажите ожидаемый вес вагона для резерва места",
            "reserve_amount_required",
        )
    shortage: int | None = None
    with transaction.atomic():
        locked = Silo.objects.select_for_update().get(pk=silo.pk)
        if locked.status != "active":
            raise _error("Силос недоступен", "silo_inactive")
        if wagon.status == st.QUARANTINE and not locked.is_quarantine:
            raise _error(
                "Карантинный вагон можно направить только в карантинный силос",
                "quarantine_silo_required",
            )
        free = locked.free_capacity_kg
        if free < amount:
            shortage = free
        else:
            SiloReservation.objects.update_or_create(
                wagon=wagon,
                defaults={"silo": locked, "amount_kg": amount, "active": True},
            )
            wagon.assigned_silo = locked
            wagon.unloading_point = locked.unloading_line
            wagon.save(update_fields=["assigned_silo", "unloading_point"])
            _set_status(
                wagon,
                target,
                user,
                f"Вагон {wagon.number} направлен в силос «{locked.name}»",
                silo_id=locked.pk,
                reserved_kg=amount,
            )
    if shortage is not None:
        # Статус фиксируем ВНЕ атомарного блока: он должен пережить ошибку,
        # которую мы поднимаем для вызывающего.
        _set_status(
            wagon,
            st.INSUFFICIENT_CAPACITY,
            user,
            f"В силосе «{silo.name}» нет места под вагон {wagon.number}",
        )
        raise _error(
            f"В силосе «{silo.name}» свободно {shortage} кг — "
            f"меньше требуемых {amount} кг",
            "insufficient_capacity",
        )
    return wagon


@transaction.atomic
def change_silo(wagon: Wagon, new_silo: Silo, reason: str, user) -> Wagon:
    """Смена силоса во время процесса — с историей и пере-резервом."""
    if wagon.status not in {st.SILO_ASSIGNED, st.UNLOADING}:
        raise _error(
            "Менять силос можно только до завершения разгрузки",
            "silo_change_not_allowed",
        )
    if not reason:
        raise _error("Укажите причину смены силоса", "silo_change_reason")
    old = wagon.assigned_silo
    new_silo = Silo.objects.select_for_update().get(pk=new_silo.pk)
    reservation = getattr(wagon, "reservation", None)
    amount = reservation.amount_kg if reservation else (wagon.planned_weight_kg or 0)
    if new_silo.free_capacity_kg < amount:
        raise _error(
            f"В силосе «{new_silo.name}» недостаточно места", "insufficient_capacity"
        )
    if reservation:
        reservation.silo = new_silo
        reservation.save(update_fields=["silo"])
    wagon.assigned_silo = new_silo
    wagon.unloading_point = new_silo.unloading_line
    wagon.save(update_fields=["assigned_silo", "unloading_point"])
    _log(
        wagon,
        "silo_change",
        f"Вагон {wagon.number}: силос «{old.name if old else '—'}» → "
        f"«{new_silo.name}» ({reason})",
        user,
        old_silo_id=old.pk if old else None,
        new_silo_id=new_silo.pk,
        reason=reason,
    )
    return wagon


# ── Разгрузка ───────────────────────────────────────────────────────────────


@transaction.atomic
def start_unloading(wagon: Wagon, user) -> Wagon:
    ensure_transition(wagon, st.UNLOADING)
    wagon.unloading_started_at = timezone.now()
    wagon.unloading_paused = False
    wagon.save(update_fields=["unloading_started_at", "unloading_paused"])
    _set_status(
        wagon,
        st.UNLOADING,
        user,
        f"Разгрузка вагона {wagon.number} начата",
        silo_id=wagon.assigned_silo_id,
    )
    return wagon


def set_unloading_paused(wagon: Wagon, paused: bool, user) -> Wagon:
    if wagon.status != st.UNLOADING:
        raise _error("Вагон сейчас не разгружается", "wagon_not_unloading")
    wagon.unloading_paused = paused
    wagon.save(update_fields=["unloading_paused"])
    _log(
        wagon,
        "unloading",
        f"Разгрузка вагона {wagon.number} "
        f"{'приостановлена' if paused else 'продолжена'}",
        user,
        paused=paused,
    )
    return wagon


@transaction.atomic
def finish_unloading(wagon: Wagon, user, note: str = "") -> Wagon:
    ensure_transition(wagon, st.UNLOADING_COMPLETED)
    wagon.unloading_finished_at = timezone.now()
    wagon.unloading_paused = False
    if note:
        wagon.note = f"{wagon.note}\n{note}".strip()
    wagon.save(update_fields=["unloading_finished_at", "unloading_paused", "note"])
    _set_status(
        wagon,
        st.UNLOADING_COMPLETED,
        user,
        f"Разгрузка вагона {wagon.number} завершена",
        silo_id=wagon.assigned_silo_id,
    )
    return wagon


# ── Тара, нетто и расхождения ──────────────────────────────────────────────


def _discrepancy_percent(wagon: Wagon) -> Decimal | None:
    """Отклонение нетто от документов; None — сверять не с чем."""
    base = wagon.document_weight_kg or wagon.expected_weight_kg
    if not base or wagon.net_weight_kg is None:
        return None
    difference = Decimal(wagon.net_weight_kg - base)
    return (difference / Decimal(base) * 100).quantize(Decimal("0.01"))


@transaction.atomic
def record_tare(wagon: Wagon, weight_kg: int, user, **kwargs) -> Wagon:
    ensure_transition(wagon, st.TARE_WEIGHED)
    tare = _record_weighing(wagon, "tare", weight_kg, user, **kwargs)
    if wagon.gross_weight_kg is None:
        raise _error("Сначала зафиксируйте брутто", "gross_required")
    if tare >= wagon.gross_weight_kg:
        raise _error("Тара не может быть больше или равна брутто", "bad_tare")
    wagon.tare_weight_kg = tare
    wagon.net_weight_kg = wagon.gross_weight_kg - tare
    wagon.save(update_fields=["tare_weight_kg", "net_weight_kg"])
    _set_status(
        wagon,
        st.TARE_WEIGHED,
        user,
        f"Вагон {wagon.number}: тара {tare} кг, нетто {wagon.net_weight_kg} кг",
    )

    percent = _discrepancy_percent(wagon)
    allowed = GrainSettings.get().allowed_discrepancy_percent
    if percent is not None and abs(percent) > allowed:
        _set_status(
            wagon,
            st.WEIGHT_DISCREPANCY,
            user,
            f"Вагон {wagon.number}: расхождение {percent}% превышает "
            f"допустимые {allowed}%",
            discrepancy_percent=str(percent),
        )
    return wagon


def _complete_simple_wagon(wagon: Wagon, user) -> Wagon:
    """Нетто подтверждено: записать приход в силос и сразу закрыть цикл."""
    wagon.unloading_finished_at = timezone.now()
    wagon.save(update_fields=["unloading_finished_at"])
    inventory_wagon(wagon, user)
    wagon.refresh_from_db()
    register_exit(wagon, user, note="Выезд после контрольного взвешивания")
    wagon.refresh_from_db()
    return wagon


@transaction.atomic
def record_simple_exit_weight(wagon: Wagon, weight_kg: int, user, **kwargs) -> Wagon:
    """Выходные весы: рассчитать нетто, сверить ожидание и завершить приход."""
    if wagon.workflow != "simple":
        raise _error("Для вагона используется старый маршрут", "not_simple_flow")
    ensure_transition(wagon, st.TARE_WEIGHED)
    tare = _record_weighing(wagon, "tare", weight_kg, user, **kwargs)
    if wagon.gross_weight_kg is None:
        raise _error("Сначала зафиксируйте входной общий вес", "gross_required")
    if tare >= wagon.gross_weight_kg:
        raise _error(
            "Выходной вес не может быть больше или равен входному",
            "bad_tare",
        )
    wagon.tare_weight_kg = tare
    wagon.net_weight_kg = wagon.gross_weight_kg - tare
    wagon.save(update_fields=["tare_weight_kg", "net_weight_kg"])
    _set_status(
        wagon,
        st.TARE_WEIGHED,
        user,
        f"Поезд {wagon.number}: выходной вес {tare} кг, нетто {wagon.net_weight_kg} кг",
    )

    percent = _discrepancy_percent(wagon)
    allowed = GrainSettings.get().allowed_discrepancy_percent
    if percent is not None and abs(percent) > allowed:
        _set_status(
            wagon,
            st.WEIGHT_DISCREPANCY,
            user,
            f"Поезд {wagon.number}: нетто отличается от ожидаемого на {percent}%",
            discrepancy_percent=str(percent),
            expected_weight_kg=wagon.planned_weight_kg,
            actual_net_weight_kg=wagon.net_weight_kg,
        )
        return wagon
    return _complete_simple_wagon(wagon, user)


@transaction.atomic
def resolve_simple_discrepancy(
    wagon: Wagon, action: str, user, reason: str = ""
) -> Wagon:
    if wagon.workflow != "simple" or wagon.status != st.WEIGHT_DISCREPANCY:
        raise _error("У прихода нет расхождения для проверки", "no_discrepancy")
    if action == "confirm":
        if not reason:
            raise _error("Укажите причину подтверждения", "reason_required")
        _set_status(
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
        _set_status(
            wagon,
            st.AT_SILO,
            user,
            f"Поезд {wagon.number} отправлен на повторное выходное взвешивание",
            resolution="reweigh",
        )
        return wagon
    raise _error("Неизвестное действие по расхождению", "bad_resolution")


@transaction.atomic
def resolve_discrepancy(wagon: Wagon, action: str, user, reason: str = "") -> Wagon:
    if wagon.status != st.WEIGHT_DISCREPANCY:
        raise _error("У вагона нет расхождения", "no_discrepancy")
    if action == "confirm":
        if not reason:
            raise _error(
                "Укажите обоснование подтверждения фактического веса", "reason_required"
            )
        _set_status(
            wagon,
            st.TARE_WEIGHED,
            user,
            f"Расхождение по вагону {wagon.number} подтверждено: {reason}",
            resolution="confirmed",
            reason=reason,
        )
    elif action == "reweigh":
        _set_status(
            wagon,
            st.REWEIGHING_REQUIRED,
            user,
            f"Вагон {wagon.number} отправлен на повторное взвешивание",
            resolution="reweigh",
        )
    else:
        raise _error("Неизвестное действие по расхождению", "bad_resolution")
    return wagon


# ── Оприходование ──────────────────────────────────────────────────────────


def _apply_income(
    silo: Silo, amount_kg: int, wagon: Wagon, user, measurement_source: str
):
    """Записать приход в силос; вызывать только под select_for_update."""
    balance = silo.current_balance_kg
    if balance + amount_kg > silo.total_capacity_kg:
        raise _error(
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
        measurement_source=measurement_source,
        operator=user,
    )


@transaction.atomic
def inventory_wagon(wagon: Wagon, user, allocations: list[dict] | None = None) -> Wagon:
    """Оприходовать нетто в силос(ы). Идемпотентно: второй раз — ошибка."""
    wagon = Wagon.objects.select_for_update().get(pk=wagon.pk)
    ensure_transition(wagon, st.INVENTORIED)
    if wagon.movements.filter(movement_type="income").exists():
        raise _error("Вагон уже оприходован", "already_inventoried")
    if wagon.net_weight_kg is None:
        raise _error("Сначала рассчитайте нетто", "net_weight_required")

    if allocations:
        total = sum(int(part.get("amount_kg") or 0) for part in allocations)
        if total != wagon.net_weight_kg:
            raise _error(
                f"Сумма распределений {total} кг не равна нетто "
                f"{wagon.net_weight_kg} кг",
                "allocation_mismatch",
            )
        parts = allocations
    else:
        if wagon.assigned_silo_id is None:
            raise _error("Силос не назначен", "silo_required")
        parts = [
            {
                "silo_id": wagon.assigned_silo_id,
                "amount_kg": wagon.net_weight_kg,
                "measurement_source": "manual",
            }
        ]

    for part in parts:
        silo = Silo.objects.select_for_update().get(pk=part["silo_id"])
        _apply_income(
            silo,
            int(part["amount_kg"]),
            wagon,
            user,
            str(part.get("measurement_source") or "manual"),
        )

    reservation = getattr(wagon, "reservation", None)
    if reservation and reservation.active:
        reservation.active = False
        reservation.save(update_fields=["active"])

    _set_status(
        wagon,
        st.INVENTORIED,
        user,
        f"Вагон {wagon.number} оприходован: {wagon.net_weight_kg} кг",
    )
    _set_status(wagon, st.EXIT_ALLOWED, user, f"Вагону {wagon.number} разрешён выезд")
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
    ensure_transition(wagon, st.EXITED)
    wagon.exited_at = occurred_at or timezone.now()
    wagon.exit_note = note
    wagon.save(update_fields=["exited_at", "exit_note"])
    _set_status(wagon, st.EXITED, user, f"Вагон {wagon.number} выехал")
    _set_status(wagon, st.COMPLETED, user, f"Цикл вагона {wagon.number} завершён")
    supply = wagon.supply
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
        raise _error("Недопустимый тип операции", "bad_movement_type")
    if not note:
        raise _error("Укажите причину корректировки", "note_required")
    try:
        delta_kg = int(delta_kg)
    except (TypeError, ValueError):
        raise _error("Изменение должно быть целым числом кг", "bad_amount")
    if delta_kg == 0:
        raise _error("Изменение не может быть нулевым", "bad_amount")
    silo = Silo.objects.select_for_update().get(pk=silo.pk)
    balance = silo.current_balance_kg
    new_balance = balance + delta_kg
    if new_balance < 0:
        raise _error("Остаток силоса не может стать отрицательным", "negative_balance")
    if new_balance > silo.total_capacity_kg:
        raise _error("Операция переполнит силос", "silo_overflow")
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


def vehicle_plate_candidates(*, now=None) -> list[VehiclePlateEvent]:
    """Return only fresh, unclaimed events for the configured truck lane."""

    oldest, newest = _vehicle_plate_time_bounds(now)
    return list(
        VehiclePlateEvent.objects.filter(
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
        ).order_by("-detected_at", "-id")[:VEHICLE_PLATE_CANDIDATE_LIMIT]
    )


def _parse_vehicle_plate_event_id(raw_event_id) -> UUID:
    try:
        return UUID(str(raw_event_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error(
            "Некорректный идентификатор события номера машины",
            "bad_vehicle_plate_event_id",
        ) from exc


def _locked_vehicle_plate_event(raw_event_id) -> VehiclePlateEvent:
    event_id = _parse_vehicle_plate_event_id(raw_event_id)
    event = (
        VehiclePlateEvent.objects.select_for_update().filter(event_id=event_id).first()
    )
    if event is None:
        raise _error(
            "Событие номера машины недоступно",
            "vehicle_plate_event_unavailable",
        )

    oldest, newest = _vehicle_plate_time_bounds()
    is_available = (
        event.camera == VEHICLE_PLATE_CAMERA
        and event.source == VEHICLE_PLATE_SOURCE
        and event.processing_status == VehiclePlateEvent.RECEIVED
        and oldest <= event.detected_at <= newest
        and oldest <= event.received_at <= newest
        and not Wagon.objects.filter(vehicle_plate_event=event).exists()
        and not AutomaticPassageCapture.objects.filter(
            vehicle_plate_event=event
        ).exists()
    )
    if not is_available:
        raise _error(
            "Событие номера машины недоступно или уже использовано",
            "vehicle_plate_event_unavailable",
        )
    return event


def normalize_passage_number(raw_number) -> str:
    """Canonicalize a Kazakhstan plate without rewriting free-form fallback IDs."""

    number = (raw_number or "").strip()
    compact = re.sub(r"[\s-]+", "", number.upper())
    return compact if KZ_VEHICLE_PLATE_RE.fullmatch(compact) else number


def _reset_automatic_passage_lane(state: PassageScaleAutomationState) -> None:
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


def _lock_automatic_passage_lane() -> tuple[
    PassageScaleAutomationState | None,
    AutomaticPassageCapture | None,
]:
    """Lock the shared automatic lane before any manual passage row lock."""

    states = PassageScaleAutomationState.objects.select_for_update()
    if settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED:
        state, _created = states.get_or_create(
            scale_number=scale.TRUCK_SCALE_KEY,
            defaults={"phase": PassageScaleAutomationState.UNARMED},
        )
    else:
        # A rolling deploy can temporarily have an enabled monitor and a
        # disabled web worker.  Existing durable lane state remains the mutex
        # regardless of this process's local feature flag; only avoid creating
        # a brand-new state while the feature is disabled.
        state = states.filter(scale_number=scale.TRUCK_SCALE_KEY).first()
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
            _reset_automatic_passage_lane(state)
            capture = None
    return state, capture


def _assert_automatic_passage_lane_allows_manual_operation(
    state: PassageScaleAutomationState | None,
    capture: AutomaticPassageCapture | None,
) -> None:
    if state is None:
        return
    blocked = (
        capture is not None
        and capture.status == AutomaticPassageCapture.PROCESSING
    ) or state.phase in {
        PassageScaleAutomationState.STABILIZING,
        PassageScaleAutomationState.PROCESSING,
    } or (
        state.phase == PassageScaleAutomationState.AWAITING_CLEAR
        and (capture is None or capture.status != AutomaticPassageCapture.FAILED)
    )
    if blocked:
        raise _error(
            "Операцию нельзя выполнить, пока автоматические весы "
            "обрабатывают текущую машину.",
            "passage_capture_in_progress",
        )


def _fence_automatic_passage_lane_for_manual_mutation(
    state: PassageScaleAutomationState | None,
) -> None:
    """Fence a scale snapshot taken before a successful manual mutation."""

    if state is None or state.phase not in {
        PassageScaleAutomationState.UNARMED,
        PassageScaleAutomationState.ARMED,
    }:
        return
    _reset_automatic_passage_lane(state)


@transaction.atomic
def _prepare_manual_passage_scale_operation() -> None:
    state, capture = _lock_automatic_passage_lane()
    _assert_automatic_passage_lane_allows_manual_operation(state, capture)
    _fence_automatic_passage_lane_for_manual_mutation(state)


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
        raise _error("Укажите, что вывозят", "cargo_required")

    # This is the first database lock. The monitor and all manual passage
    # mutations use State -> capture/event -> Wagon ordering.
    automation_state, automatic_capture = _lock_automatic_passage_lane()
    _assert_automatic_passage_lane_allows_manual_operation(
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
            raise _error(
                f"Машина {number} уже находится на территории",
                "passage_already_on_site",
            ) from exc
        raise _error(
            "Событие номера машины уже использовано",
            "vehicle_plate_event_unavailable",
        ) from exc

    if plate_event is not None:
        plate_event.processing_status = VehiclePlateEvent.PROCESSED
        plate_event.processing_action = AUTO_ACTION_MANUAL_ENTRY
        plate_event.processing_error = ""
        plate_event.processing_started_at = None
        plate_event.processed_at = timezone.now()
        plate_event.save(
            update_fields=[
                "processing_status",
                "processing_action",
                "processing_error",
                "processing_started_at",
                "processed_at",
            ]
        )

    _log(
        wagon,
        "passage",
        f"Проход {wagon.number or f'#{wagon.pk}'}: заезд за «{cargo_name}»",
        user,
        cargo_name=cargo_name,
        vehicle_plate_event_id=(
            str(plate_event.event_id) if plate_event is not None else None
        ),
        camera_source=number_camera_source,
    )
    _fence_automatic_passage_lane_for_manual_mutation(automation_state)
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
        raise _error("Это приход, а не проход", "not_passage")
    ensure_transition(wagon, st.AT_SILO)
    wagon.gross_weight_kg = _record_weighing(wagon, "gross", weight_kg, user, **kwargs)
    wagon.silo_arrived_at = occurred_at or timezone.now()
    wagon.unloading_started_at = wagon.silo_arrived_at
    wagon.save(
        update_fields=["gross_weight_kg", "silo_arrived_at", "unloading_started_at"]
    )
    _set_status(
        wagon,
        st.AT_SILO,
        user,
        f"Проход {wagon.number or f'#{wagon.pk}'}: заезд {wagon.gross_weight_kg} кг, "
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
        raise _error("Это приход, а не проход", "not_passage")
    ensure_transition(wagon, st.TARE_WEIGHED)
    if wagon.gross_weight_kg is None:
        raise _error("Сначала зафиксируйте вес на въезде", "entry_weight_required")
    exit_weight = _record_weighing(wagon, "tare", weight_kg, user, **kwargs)
    # Обратная приходу проверка: гружёная машина обязана быть тяжелее пустой.
    if exit_weight <= wagon.gross_weight_kg:
        raise _error(
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
    _set_status(
        wagon,
        st.TARE_WEIGHED,
        user,
        f"Проход {wagon.number or f'#{wagon.pk}'}: выезд {exit_weight} кг, "
        f"вывезено {wagon.net_weight_kg} кг «{wagon.cargo_name}»",
        entry_weight_kg=wagon.gross_weight_kg,
        exit_weight_kg=exit_weight,
        net_weight_kg=wagon.net_weight_kg,
    )
    # Проход не оприходуется в силос: груз уезжает, остатки не трогаем.
    # Статусная цепочка та же, поэтому INVENTORIED проставляем явно.
    _set_status(
        wagon,
        st.INVENTORIED,
        user,
        f"Проход {wagon.number or f'#{wagon.pk}'}: вывоз зафиксирован",
    )
    _set_status(wagon, st.EXIT_ALLOWED, user, "Выезд разрешён")
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

    def as_payload(self) -> dict:
        payload = {
            "status": self.status,
            "action": self.action or None,
            "error": self.error or None,
            "wagon_id": self.wagon_id,
            "weight_kg": self.weight_kg,
        }
        if self.unassigned_id is not None:
            payload["unassigned_id"] = self.unassigned_id
        return payload


@dataclass(frozen=True, slots=True)
class _AutomationClaim:
    event_id: int
    action: str
    attempt: int


def _auto_event_is_fresh(event: VehiclePlateEvent, now) -> bool:
    oldest = now - timedelta(
        seconds=settings.VEHICLE_PLATE_AUTO_EXPORT_EVENT_MAX_AGE_SECONDS
    )
    newest = now + VEHICLE_PLATE_AUTO_MAX_FUTURE
    return (
        oldest <= event.detected_at <= newest and oldest <= event.received_at <= newest
    )


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


def _locked_auto_intent(
    event: VehiclePlateEvent,
) -> tuple[str | None, Wagon | None, str]:
    """Decide entry or exit for a recognized plate without an operator.

    An unknown plate is always a new entry, even while blank-number or
    manually created passages are on site: automation must never stop and
    wait for a human. A plate that matches exactly one on-site passage is an
    entry when that passage still waits for its empty weight and an exit when
    it already carries one.
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
        return AUTO_ACTION_ENTRY, None, ""
    if len(passages) != 1:
        return None, None, "ambiguous_active_passage"

    wagon = passages[0]
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
    minimum_exit_at = wagon.arrived_at + timedelta(
        seconds=settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS
    )
    if event.detected_at < minimum_exit_at:
        return None, wagon, "entry_exit_too_close"
    return AUTO_ACTION_EXIT, wagon, ""


@transaction.atomic
def _begin_vehicle_plate_automation(
    event_pk: int,
    *,
    now,
    allow_capture: bool,
    expected_camera: str = VEHICLE_PLATE_CAMERA,
    expected_source: str = VEHICLE_PLATE_SOURCE,
    durable_scale_sample: bool = False,
) -> VehiclePlateAutomationResult | _AutomationClaim:
    hint = VehiclePlateEvent.objects.get(pk=event_pk)
    _lock_auto_lane_mutex(hint)
    event = VehiclePlateEvent.objects.select_for_update().get(pk=event_pk)

    if event.processing_status in (
        VehiclePlateEvent.PROCESSED,
        VehiclePlateEvent.FAILED,
    ):
        return _terminal_automation_result(event)

    if event.camera != expected_camera or event.source != expected_source:
        event.processing_attempts += 1
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.PROCESSED,
            action=AUTO_ACTION_IGNORED,
            error="wrong_lane",
            now=now,
        )
        return _terminal_automation_result(event, already_processed=False)

    # The live reading belongs only to the HTTP request that created this
    # durable row. A duplicate cannot safely reconstruct the missed physical
    # capture window after a crash between INSERT and processing.
    if event.processing_status == VehiclePlateEvent.RECEIVED and not allow_capture:
        event.processing_attempts += 1
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.FAILED,
            action=event.processing_action,
            error="capture_window_missed",
            now=now,
        )
        return _terminal_automation_result(event, already_processed=False)

    if not durable_scale_sample and not _auto_event_is_fresh(event, now):
        event.processing_attempts += 1
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.FAILED,
            action=event.processing_action,
            error="event_stale",
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
        if durable_scale_sample and event.processing_action in {
            AUTO_ACTION_ENTRY,
            AUTO_ACTION_EXIT,
        }:
            # Unlike the legacy webhook path, the automatic scale coordinator
            # has already persisted the exact physical sample.  Reclaiming a
            # stale DB apply is therefore safe and must not read the scale or
            # ask Camera-PC to create a second recognition request.
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
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.FAILED,
            action=event.processing_action,
            error="processing_interrupted",
            now=now,
        )
        return _terminal_automation_result(event, already_processed=False)

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
        event.processing_attempts += 1
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.FAILED,
            action=event.processing_action,
            error="lane_busy",
            now=now,
        )
        return _terminal_automation_result(event, already_processed=False)

    action, _wagon, error = _locked_auto_intent(event)
    if error:
        event.processing_attempts += 1
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.FAILED,
            action=event.processing_action,
            error=error,
            now=now,
        )
        return _terminal_automation_result(event, already_processed=False)

    event.processing_status = VehiclePlateEvent.PROCESSING
    event.processing_attempts += 1
    event.processing_action = action or ""
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
        action=action or "",
        attempt=event.processing_attempts,
    )


def _safe_scale_error(exc: APIException) -> str:
    code = exc.get_codes()
    if not isinstance(code, str):
        code = getattr(exc, "default_code", "truck_scale_error")
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(code).lower()).strip("_")
    return (normalized or "truck_scale_error")[:64]


@transaction.atomic
def _record_auto_scale_failure(
    claim: _AutomationClaim,
    *,
    error: str,
    now,
) -> VehiclePlateAutomationResult:
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
    _finish_auto_event(
        event,
        status=VehiclePlateEvent.FAILED,
        action=event.processing_action,
        error=error,
        now=now,
    )
    return _terminal_automation_result(event, already_processed=False)


def _auto_scale_kwargs(reading: scale.ScaleReading) -> dict:
    return {
        "source": "scale",
        "scale_number": scale.TRUCK_SCALE_KEY,
        "scale_age_seconds": reading.age_seconds,
        "scale_updated_at": reading.updated_at,
    }


@transaction.atomic
def _apply_vehicle_plate_automation(
    claim: _AutomationClaim,
    *,
    reading: scale.ScaleReading,
    weight_kg: int,
    user,
    photo_request_id=None,
    photo_camera: str = "",
) -> VehiclePlateAutomationResult:
    scale.configure_authoritative_db_timeouts()
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

    action, wagon, intent_error = _locked_auto_intent(event)
    if intent_error or action != claim.action:
        _finish_auto_event(
            event,
            status=VehiclePlateEvent.FAILED,
            action=event.processing_action,
            error=intent_error or "passage_state_changed",
        )
        return _terminal_automation_result(event, already_processed=False)

    kwargs = {
        **_auto_scale_kwargs(reading),
        "photo_request_id": photo_request_id,
        "photo_camera": photo_camera,
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
                _finish_auto_event(
                    event,
                    status=VehiclePlateEvent.FAILED,
                    action=action,
                    error="ambiguous_active_passage",
                )
                return _terminal_automation_result(event, already_processed=False)
            _log(
                wagon,
                "passage",
                f"Проход {wagon.number}: автоматический заезд за «{wagon.cargo_name}»",
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
                _finish_auto_event(
                    event,
                    status=VehiclePlateEvent.FAILED,
                    action=action,
                    error="vehicle_plate_event_unavailable",
                )
                return _terminal_automation_result(event, already_processed=False)
            _log(
                wagon,
                "passage",
                f"Проход {wagon.number}: автоматический заезд по заранее "
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
        if wagon is None or weight_kg <= (wagon.entry_weight_kg or 0):
            _finish_auto_event(
                event,
                status=VehiclePlateEvent.FAILED,
                action=action or AUTO_ACTION_EXIT,
                error="exit_weight_not_greater",
            )
            return _terminal_automation_result(event, already_processed=False)
        try:
            with transaction.atomic():
                wagon.exit_vehicle_plate_event = event
                wagon.save(update_fields=["exit_vehicle_plate_event"])
        except IntegrityError:
            _finish_auto_event(
                event,
                status=VehiclePlateEvent.FAILED,
                action=action or AUTO_ACTION_EXIT,
                error="vehicle_plate_event_unavailable",
            )
            return _terminal_automation_result(event, already_processed=False)
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
        action=action or "",
    )
    return VehiclePlateAutomationResult(
        status="processed",
        action=action or "",
        wagon_id=wagon.pk if wagon is not None else None,
        weight_kg=weight_kg,
    )


def process_vehicle_plate_event(
    event_pk: int,
    *,
    user=None,
    allow_capture: bool = False,
) -> VehiclePlateAutomationResult:
    """Process one webhook delivery with at most one live truck-scale read.

    Only the request that inserted the durable event may claim its physical
    capture. A short committed lease lets a concurrent duplicate observe the
    in-flight attempt without reading again. Capture failures are terminal for
    that UUID, and no database lock is held during physical HTTP I/O.
    """

    if not settings.VEHICLE_PLATE_AUTO_EXPORT_ENABLED:
        return VehiclePlateAutomationResult(status="disabled")

    claim_or_result = _begin_vehicle_plate_automation(
        event_pk,
        now=timezone.now(),
        allow_capture=allow_capture,
    )
    if isinstance(claim_or_result, VehiclePlateAutomationResult):
        return claim_or_result

    try:
        with scale.authoritative_capture(scale.TRUCK_SCALE_KEY):
            reading = scale.read_truck_scale(scale.TRUCK_SCALE_KEY)
            weight_kg = _whole_scale_weight_kg(reading)
            return _apply_vehicle_plate_automation(
                claim_or_result,
                reading=reading,
                weight_kg=weight_kg,
                user=user,
            )
    except APIException as exc:
        return _record_auto_scale_failure(
            claim_or_result,
            error=_safe_scale_error(exc),
            now=timezone.now(),
        )
    except (OperationalError, InterfaceError):
        return _record_auto_scale_failure(
            claim_or_result,
            error="truck_scale_apply_unavailable",
            now=timezone.now(),
        )


def apply_automatic_passage_scale_sample(
    event_pk: int,
    *,
    reading: scale.ScaleReading,
    user=None,
    photo_request_id=None,
    photo_camera: str = "",
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
        allow_capture=True,
        expected_camera=settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA,
        expected_source=settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE,
        durable_scale_sample=True,
    )
    if isinstance(claim_or_result, VehiclePlateAutomationResult):
        return claim_or_result
    return _apply_vehicle_plate_automation(
        claim_or_result,
        reading=reading,
        weight_kg=_whole_scale_weight_kg(reading),
        user=user,
        photo_request_id=photo_request_id,
        photo_camera=photo_camera,
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
) -> VehiclePlateAutomationResult:
    """Apply a durable scale sample whose plate could not be recognized.

    With no open passage on site the truck can only be a new entry, so a
    passage without a number is created and weighed; the operator fills in
    the plate later. With open passages the weight may be somebody's exit and
    guessing would corrupt accounting, so it is parked as an unassigned
    weighing together with its photo. Either way the lane is released.
    """

    weight_kg = _whole_scale_weight_kg(reading)
    kwargs = {
        **_auto_scale_kwargs(reading),
        "photo_request_id": request_id,
        "photo_camera": camera,
    }
    open_passages = list(
        Wagon.objects.select_for_update(of=("self",))
        .filter(direction=Wagon.PASSAGE, status__in=st.ON_SITE_STATUSES)
        .order_by("id")
        .values_list("id", flat=True)
    )
    if not open_passages:
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
        _log(
            wagon,
            "passage",
            f"Проход #{wagon.pk}: автоматический заезд, номер не распознан",
            user,
            cargo_name=wagon.cargo_name,
            camera_source=camera,
            auto=True,
            plate_unresolved=True,
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

    item = UnassignedWeighing.objects.create(
        capture=capture,
        weight_kg=weight_kg,
        stable_weight_at=stable_weight_at,
        scale_number=scale.TRUCK_SCALE_KEY,
        scale_age_seconds=reading.age_seconds,
        scale_updated_at=reading.updated_at or "",
        camera=camera,
        photo_request_id=request_id,
        reason="open_passages_exist",
    )
    log_event(
        "grain_unassigned_weighing",
        f"Взвешивание {weight_kg} кг без номера: на территории "
        f"{len(open_passages)} маш., нужна привязка к рейсу",
        user=user,
        payload={
            "unassigned_id": item.pk,
            "weight_kg": weight_kg,
            "open_passage_ids": open_passages,
            "camera_source": camera,
            "auto": True,
        },
    )
    return VehiclePlateAutomationResult(
        status="processed",
        action=AUTO_ACTION_UNASSIGNED,
        weight_kg=weight_kg,
        unassigned_id=item.pk,
    )


def _unassigned_scale_kwargs(item: UnassignedWeighing) -> dict:
    return {
        "source": "scale",
        "scale_number": item.scale_number,
        "scale_age_seconds": item.scale_age_seconds,
        "scale_updated_at": item.scale_updated_at or None,
        "photo_request_id": item.photo_request_id,
        "photo_camera": item.camera,
    }


def _move_unassigned_photo(item: UnassignedWeighing, wagon: Wagon, kind: str) -> None:
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


@transaction.atomic
def assign_unassigned_weighing(
    item: UnassignedWeighing,
    wagon: Wagon,
    user,
) -> UnassignedWeighing:
    """Attach a parked weight to the passage the operator points at."""

    item = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if item.status != UnassignedWeighing.OPEN:
        raise _error("Это взвешивание уже обработано", "unassigned_weighing_resolved")
    wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon.pk)
    if not wagon.is_passage:
        raise _error("Привязать взвешивание можно только к вывозу", "not_passage")
    kwargs = _unassigned_scale_kwargs(item)
    if wagon.status == st.ARRIVED and wagon.gross_weight_kg is None:
        record_passage_entry_weight(
            wagon, item.weight_kg, user, occurred_at=item.stable_weight_at, **kwargs
        )
        action, kind = AUTO_ACTION_ENTRY, "gross"
    elif wagon.status == st.AT_SILO and wagon.tare_weight_kg is None:
        record_passage_exit_weight(
            wagon, item.weight_kg, user, occurred_at=item.stable_weight_at, **kwargs
        )
        action, kind = AUTO_ACTION_EXIT, "tare"
    else:
        raise _error("Этот рейс сейчас не ждёт взвешивания", "wagon_not_awaiting_weight")
    _move_unassigned_photo(item, wagon, kind)
    item.status = UnassignedWeighing.ASSIGNED
    item.wagon = wagon
    item.action = action
    item.resolved_by = user
    item.resolved_at = timezone.now()
    item.save(update_fields=["status", "wagon", "action", "resolved_by", "resolved_at"])
    _log(
        wagon,
        "unassigned_weighing",
        f"Проход {wagon.number or f'#{wagon.pk}'}: привязано взвешивание "
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

    locked = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if locked.status != UnassignedWeighing.OPEN:
        raise _error("Это взвешивание уже обработано", "unassigned_weighing_resolved")
    wagon = create_passage(
        user,
        number=number,
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
        raise _error("Это взвешивание уже обработано", "unassigned_weighing_resolved")
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
def set_passage_number(wagon: Wagon, raw_number, user) -> Wagon:
    """Fill in or correct the plate of a passage the camera could not read."""

    wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon.pk)
    if not wagon.is_passage:
        raise _error("Номер можно менять только у вывоза", "not_passage")
    if wagon.status in st.TERMINAL_STATUSES:
        raise _error("Рейс уже завершён", "wagon_finished")
    number = normalize_passage_number(raw_number)
    if not number:
        raise _error("Укажите номер машины", "number_required")
    if len(number) > 30:
        raise _error("Номер слишком длинный", "bad_number")
    previous = wagon.number
    if previous == number:
        return wagon
    wagon.number = number
    wagon.number_source = "manual"
    try:
        with transaction.atomic():
            wagon.save(update_fields=["number", "number_source"])
    except IntegrityError as exc:
        raise _error(
            f"Машина {number} уже находится на территории",
            "passage_already_on_site",
        ) from exc
    _log(
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
        raise _error("Причина удаления должна быть строкой", "bad_delete_reason")
    normalized = " ".join(reason.split())
    if normalized and len(normalized) > DELETE_REASON_MAX_LENGTH:
        raise _error(
            f"Причина удаления не должна превышать {DELETE_REASON_MAX_LENGTH} символов",
            "delete_reason_too_long",
        )
    return normalized


def _is_active_deletable_wagon(wagon: Wagon) -> bool:
    return wagon.status in st.ON_SITE_STATUSES


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
    automatic_lane_capture = None
    if wagon.is_passage:
        # This singleton is the mutex shared with the poller's edge detector.
        # It must be locked before Wagon even in a mixed-version/flag rollout,
        # otherwise State -> capture -> Wagon can deadlock with Wagon -> capture.
        automation_state, automatic_lane_capture = _lock_automatic_passage_lane()
        _assert_automatic_passage_lane_allows_manual_operation(
            automation_state,
            automatic_lane_capture,
        )
    try:
        wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon.pk)
    except Wagon.DoesNotExist as exc:
        raise NotFound(
            {"detail": "Рейс уже удалён", "code": "wagon_not_found"}
        ) from exc
    automatic_capture = (
        automatic_lane_capture
        if automatic_lane_capture is not None
        and automatic_lane_capture.status == AutomaticPassageCapture.PROCESSING
        else None
    )
    if wagon.is_passage and automatic_capture is None:
        automatic_capture = (
            AutomaticPassageCapture.objects.select_for_update()
            .filter(status=AutomaticPassageCapture.PROCESSING)
            .only("pk")
            .first()
        )
    if automatic_capture is not None:
        # OCR may not have produced a plate yet, so the durable capture cannot
        # safely be tied to one wagon here. Conservatively freeze passage
        # deletion for the short processing window; otherwise a concurrent
        # exit could be reinterpreted as a new entry after this wagon vanished.
        raise _error(
            "Рейс нельзя удалить, пока автоматические весы обрабатывают машину.",
            "passage_capture_in_progress",
        )
    processing_capture = (
        PassageWeightCapture.objects.select_for_update()
        .filter(wagon=wagon, status=PassageWeightCapture.PROCESSING)
        .only("pk")
        .first()
    )
    if processing_capture is not None:
        raise _error(
            "Рейс нельзя удалить, пока фиксируются вес и номер машины.",
            "passage_capture_in_progress",
        )
    supply = (
        GrainSupply.objects.select_for_update().get(pk=wagon.supply_id)
        if wagon.supply_id is not None
        else None
    )
    reason = _normalized_delete_reason(reason)
    active_deletion = _is_active_deletable_wagon(wagon)
    finished = wagon.status in st.TERMINAL_STATUSES or wagon.status == st.EXITED
    if not finished and not active_deletion:
        raise _error(
            "Рейс нельзя удалить на текущем этапе. "
            "Сначала завершите текущую физическую операцию.",
            "wagon_delete_not_allowed",
        )
    if active_deletion and len(reason) < DELETE_REASON_MIN_LENGTH:
        raise _error(
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
        raise _error(
            "Подтвердите, что физически разгруженное зерно уже учтено "
            "отдельно либо фактической разгрузки не было",
            "unrecorded_grain_confirmation_required",
        )

    label = wagon.number or f"#{wagon.pk}"
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
            note=(f"Откат прихода рейса {label}" + (f": {reason}" if reason else "")),
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
        f"Рейс {label} удалён"
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
    _fence_automatic_passage_lane_for_manual_mutation(automation_state)
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
            return _arrive_expected_wagon(expected, user, camera_source)
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
    _log(
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


def _arrive_expected_wagon(wagon: Wagon, user, camera_source: str) -> Wagon:
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
    _set_status(
        wagon,
        st.ARRIVED,
        user,
        f"Камера распознала вагон {wagon.number}: прибытие по ожидаемому приходу",
        camera_source=camera_source,
        auto=True,
    )
    return wagon
