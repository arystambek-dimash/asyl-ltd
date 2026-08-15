"""Бизнес-операции прихода зерна. Статусы меняются только здесь.

Каждая операция атомарна, силос блокируется ``select_for_update`` перед
резервом и оприходованием — два вагона не займут одно и то же место.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.eventlog.services import log_event
from . import scale
from . import statuses as st
from .models import (
    GrainMovement,
    GrainSettings,
    GrainSupply,
    LabCheck,
    Silo,
    SiloAllocation,
    SiloReservation,
    SiloType,
    Wagon,
    WeighingRecord,
)


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

    Network I/O intentionally happens before the database transaction: a slow
    scale must not hold a row lock.  The write phase locks and reloads the
    wagon, so concurrent double clicks cannot record the same transition twice.
    """
    _ensure_scale_action_ready(wagon, action)
    expected_status = wagon.status
    reading = scale.read_truck_scale()
    return _store_scale_weight(
        wagon.pk,
        action,
        reading,
        user,
        expected_status=expected_status,
    )


@transaction.atomic
def _store_scale_weight(
    wagon_id: int,
    action: str,
    reading: scale.ScaleReading,
    user,
    *,
    expected_status: str,
) -> Wagon:
    # Lock only the wagon row. Nullable joins cannot be locked by PostgreSQL,
    # and related objects are loaded lazily where a transition needs them.
    wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon_id)
    if wagon.status != expected_status:
        raise _error(
            "Состояние вагона изменилось во время чтения весов — повторите взвешивание",
            "wagon_changed_during_scale_read",
        )
    _ensure_scale_action_ready(wagon, action)
    kwargs = {
        "source": "scale",
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
def register_exit(wagon: Wagon, user, note: str = "") -> Wagon:
    ensure_transition(wagon, st.EXITED)
    wagon.exited_at = timezone.now()
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


@transaction.atomic
def create_passage(user, *, number="", cargo_name="", note="", **kwargs) -> Wagon:
    """Зарегистрировать проход: машина уже на территории, ждёт входных весов."""
    cargo_name = (cargo_name or "").strip()
    if not cargo_name:
        raise _error("Укажите, что вывозят", "cargo_required")
    wagon = Wagon.objects.create(
        supply=None,
        number=(number or "").strip(),
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name=cargo_name,
        status=st.ARRIVED,
        arrived_at=timezone.now(),
        arrived_by=user,
        number_source=kwargs.get("number_source") or "manual",
        number_camera_source=kwargs.get("number_camera_source") or "",
        note=note or "",
    )
    _log(
        wagon,
        "passage",
        f"Проход {wagon.number or f'#{wagon.pk}'}: заезд за «{cargo_name}»",
        user,
        cargo_name=cargo_name,
    )
    return wagon


@transaction.atomic
def record_passage_entry_weight(wagon: Wagon, weight_kg: int, user, **kwargs) -> Wagon:
    """Весы на въезде: машина пустая. Дальше её грузят."""
    if not wagon.is_passage:
        raise _error("Это приход, а не проход", "not_passage")
    ensure_transition(wagon, st.AT_SILO)
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
        f"Проход {wagon.number or f'#{wagon.pk}'}: заезд {wagon.gross_weight_kg} кг, "
        f"загрузка «{wagon.cargo_name}»",
        entry_weight_kg=wagon.gross_weight_kg,
    )
    return wagon


@transaction.atomic
def record_passage_exit_weight(wagon: Wagon, weight_kg: int, user, **kwargs) -> Wagon:
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
            "Вес на выезде должен быть больше веса на въезде: "
            "машина уезжает гружёной",
            "bad_exit_weight",
        )
    wagon.tare_weight_kg = exit_weight
    wagon.net_weight_kg = wagon.computed_net_kg()
    wagon.save(update_fields=["tare_weight_kg", "net_weight_kg"])
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
    return register_exit(wagon, user, note="Выезд после загрузки")


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
    try:
        wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=wagon.pk)
    except Wagon.DoesNotExist as exc:
        raise NotFound(
            {"detail": "Рейс уже удалён", "code": "wagon_not_found"}
        ) from exc
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
    reservation = SiloReservation.objects.filter(wagon=wagon).values(
        "id", "silo_id", "amount_kg", "active"
    ).first()
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
            note=(
                f"Откат прихода рейса {label}"
                + (f": {reason}" if reason else "")
            ),
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
# Модель находит табличку, но НЕ читает цифры — OCR появится отдельно. Поэтому
# рейс заводится без номера: важен сам факт и время заезда, а номер допишет
# оператор или будущий OCR.

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
    user=None, *, camera_source: str = "", number: str = "",
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
            number=number, status__in=st.ON_SITE_STATUSES,
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
        + (f": вагон {number}" if number
           else ". Номер не распознан — укажите его вручную."),
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
    wagon.save(update_fields=[
        "arrived_at", "arrived_by", "number_source", "number_camera_source",
    ])
    _set_status(
        wagon,
        st.ARRIVED,
        user,
        f"Камера распознала вагон {wagon.number}: прибытие по ожидаемому приходу",
        camera_source=camera_source,
        auto=True,
    )
    return wagon
