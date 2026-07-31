"""Бизнес-операции прихода зерна. Статусы меняются только здесь.

Каждая операция атомарна, силос блокируется ``select_for_update`` перед
резервом и оприходованием — два вагона не займут одно и то же место.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from . import statuses as st
from .models import (
    GrainMovement, GrainSettings, GrainSupply, LabCheck, Silo, SiloAllocation,
    SiloReservation, Wagon, WeighingRecord,
)


def _error(detail: str, code: str) -> ValidationError:
    return ValidationError({"detail": detail, "code": code})


def _log(wagon: Wagon, event: str, message: str, user, **payload):
    log_event(
        f"grain_{event}", message, user=user,
        payload={
            "wagon_id": wagon.pk, "wagon_number": wagon.number,
            "supply_id": wagon.supply_id, "status": wagon.status, **payload,
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
    _log(wagon, "status", message, user, old_status=old, new_status=target,
         **payload)


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
        "grain_supply", f"Поставка #{supply.pk} от «{supply.supplier}» ожидается",
        user=user, payload={"supply_id": supply.pk, "action": "published"},
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
            number=number, status__in=st.ON_SITE_STATUSES | {st.EXPECTED},
        ).exists():
            raise _error(
                f"Вагон {number} уже заявлен или на территории",
                "wagon_number_busy",
            )
        wagon = Wagon.objects.create(supply=supply, number=number)
        _log(wagon, "supply", f"Вагон {number} добавлен в поставку #{supply.pk}",
             user)
        created.append(wagon)
    return created


# ── Прибытие ────────────────────────────────────────────────────────────────

@transaction.atomic
def register_arrival(number: str, user, supply: GrainSupply | None = None) -> Wagon:
    number = (number or "").strip()
    if not number:
        raise _error("Укажите номер вагона", "wagon_number_required")
    if Wagon.objects.filter(
        number=number, status__in=st.ON_SITE_STATUSES,
    ).exists():
        raise _error(
            f"Вагон {number} уже зарегистрирован на территории",
            "wagon_already_on_site",
        )

    wagon = (Wagon.objects.select_for_update()
             .filter(number=number, status=st.EXPECTED)
             .order_by("id").first())
    if wagon is None and supply is not None:
        # Номер не был известен заранее — добавляем вагон к поставке сейчас.
        wagon = Wagon.objects.create(
            supply=supply, number=number, status=st.EXPECTED)
        _log(wagon, "arrival",
             f"Вагон {number} добавлен к поставке #{supply.pk} при прибытии",
             user)
    if wagon is None:
        # Незапланированное прибытие: до решения диспетчера на разгрузку нельзя.
        wagon = Wagon.objects.create(
            number=number, status=st.UNPLANNED, unplanned=True)
        _set_status(wagon, st.WAITING_FOR_APPROVAL, user,
                    f"Незапланированный вагон {number} ждёт подтверждения")
        return wagon

    wagon.arrived_at = timezone.now()
    wagon.arrived_by = user
    wagon.save(update_fields=["arrived_at", "arrived_by"])
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
    _set_status(wagon, st.ARRIVED, user,
                f"Незапланированный вагон {wagon.number} подтверждён диспетчером")
    return wagon


# ── Взвешивания ─────────────────────────────────────────────────────────────

def _record_weighing(wagon: Wagon, kind: str, weight_kg: int, user, *,
                     scale_number="", source="manual", manual_reason=""):
    try:
        weight_kg = int(weight_kg)
    except (TypeError, ValueError):
        raise _error("Вес должен быть целым числом килограммов", "bad_weight")
    if weight_kg <= 0:
        raise _error("Вес должен быть положительным", "bad_weight")
    if source == "manual" and not manual_reason:
        raise _error(
            "Для ручного ввода веса укажите причину", "manual_reason_required")
    previous = wagon.gross_weight_kg if kind == "gross" else wagon.tare_weight_kg
    WeighingRecord.objects.create(
        wagon=wagon, kind=kind, weight_kg=weight_kg,
        scale_number=scale_number, source=source,
        manual_reason=manual_reason, previous_weight_kg=previous,
        operator=user,
    )
    _log(wagon, "weighing",
         f"Вагон {wagon.number}: {'брутто' if kind == 'gross' else 'тара'} "
         f"{weight_kg} кг",
         user, kind=kind, weight_kg=weight_kg, source=source,
         previous_weight_kg=previous, manual_reason=manual_reason)
    return weight_kg


@transaction.atomic
def record_gross(wagon: Wagon, weight_kg: int, user, **kwargs) -> Wagon:
    ensure_transition(wagon, st.GROSS_WEIGHED)
    wagon.gross_weight_kg = _record_weighing(
        wagon, "gross", weight_kg, user, **kwargs)
    wagon.save(update_fields=["gross_weight_kg"])
    _set_status(wagon, st.GROSS_WEIGHED, user,
                f"Вагон {wagon.number}: брутто зафиксировано")
    # Лаборатория обязательна для каждого вагона — очередь встаёт сразу.
    _set_status(wagon, st.LAB_PENDING, user,
                f"Вагон {wagon.number} ждёт лабораторию")
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
        wagon=wagon, decision=decision, checked_by=user, **fields)
    _set_status(wagon, target, user,
                f"Лаборатория: вагон {wagon.number} — {decision}",
                decision=decision)
    return check


# ── Силосы: подбор и резерв ────────────────────────────────────────────────

def suggest_silos(wagon: Wagon):
    """Подходящие силосы: культура/класс/место/статус/линия."""
    culture = wagon.supply.culture if wagon.supply else ""
    grain_class = wagon.supply.grain_class if wagon.supply else ""
    need = wagon.planned_weight_kg or 0
    silos = Silo.objects.filter(status="active")
    if wagon.status == st.QUARANTINE:
        silos = silos.filter(is_quarantine=True)
    else:
        silos = silos.filter(is_quarantine=False)
    suitable = []
    for silo in silos:
        if silo.grain_culture and culture and silo.grain_culture != culture:
            continue
        if (silo.grain_class and grain_class
                and silo.grain_class != grain_class
                and not silo.allow_mixing):
            continue
        if silo.free_capacity_kg < need:
            continue
        suitable.append(silo)
    return suitable


def assign_silo(wagon: Wagon, silo: Silo, user,
                expected_kg: int | None = None) -> Wagon:
    target = st.SILO_ASSIGNED
    ensure_transition(wagon, target)
    # Резерв: явный ввод → вес по документам/ожиданиям → брутто (нетто всегда
    # меньше брутто, так что бронь по брутто безопасна).
    amount = int(
        expected_kg or wagon.planned_weight_kg or wagon.gross_weight_kg or 0)
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
            _set_status(wagon, target, user,
                        f"Вагон {wagon.number} направлен в силос «{locked.name}»",
                        silo_id=locked.pk, reserved_kg=amount)
    if shortage is not None:
        # Статус фиксируем ВНЕ атомарного блока: он должен пережить ошибку,
        # которую мы поднимаем для вызывающего.
        _set_status(wagon, st.INSUFFICIENT_CAPACITY, user,
                    f"В силосе «{silo.name}» нет места под вагон {wagon.number}")
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
        raise _error("Менять силос можно только до завершения разгрузки",
                     "silo_change_not_allowed")
    if not reason:
        raise _error("Укажите причину смены силоса", "silo_change_reason")
    old = wagon.assigned_silo
    new_silo = Silo.objects.select_for_update().get(pk=new_silo.pk)
    reservation = getattr(wagon, "reservation", None)
    amount = reservation.amount_kg if reservation else (
        wagon.planned_weight_kg or 0)
    if new_silo.free_capacity_kg < amount:
        raise _error(
            f"В силосе «{new_silo.name}» недостаточно места", "insufficient_capacity")
    if reservation:
        reservation.silo = new_silo
        reservation.save(update_fields=["silo"])
    wagon.assigned_silo = new_silo
    wagon.unloading_point = new_silo.unloading_line
    wagon.save(update_fields=["assigned_silo", "unloading_point"])
    _log(wagon, "silo_change",
         f"Вагон {wagon.number}: силос «{old.name if old else '—'}» → "
         f"«{new_silo.name}» ({reason})",
         user, old_silo_id=old.pk if old else None,
         new_silo_id=new_silo.pk, reason=reason)
    return wagon


# ── Разгрузка ───────────────────────────────────────────────────────────────

@transaction.atomic
def start_unloading(wagon: Wagon, user) -> Wagon:
    ensure_transition(wagon, st.UNLOADING)
    wagon.unloading_started_at = timezone.now()
    wagon.unloading_paused = False
    wagon.save(update_fields=["unloading_started_at", "unloading_paused"])
    _set_status(wagon, st.UNLOADING, user,
                f"Разгрузка вагона {wagon.number} начата",
                silo_id=wagon.assigned_silo_id)
    return wagon


def set_unloading_paused(wagon: Wagon, paused: bool, user) -> Wagon:
    if wagon.status != st.UNLOADING:
        raise _error("Вагон сейчас не разгружается", "wagon_not_unloading")
    wagon.unloading_paused = paused
    wagon.save(update_fields=["unloading_paused"])
    _log(wagon, "unloading",
         f"Разгрузка вагона {wagon.number} "
         f"{'приостановлена' if paused else 'продолжена'}",
         user, paused=paused)
    return wagon


@transaction.atomic
def finish_unloading(wagon: Wagon, user, note: str = "") -> Wagon:
    ensure_transition(wagon, st.UNLOADING_COMPLETED)
    wagon.unloading_finished_at = timezone.now()
    wagon.unloading_paused = False
    if note:
        wagon.note = f"{wagon.note}\n{note}".strip()
    wagon.save(update_fields=[
        "unloading_finished_at", "unloading_paused", "note"])
    _set_status(wagon, st.UNLOADING_COMPLETED, user,
                f"Разгрузка вагона {wagon.number} завершена",
                silo_id=wagon.assigned_silo_id)
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
    _set_status(wagon, st.TARE_WEIGHED, user,
                f"Вагон {wagon.number}: тара {tare} кг, "
                f"нетто {wagon.net_weight_kg} кг")

    percent = _discrepancy_percent(wagon)
    allowed = GrainSettings.get().allowed_discrepancy_percent
    if percent is not None and abs(percent) > allowed:
        _set_status(
            wagon, st.WEIGHT_DISCREPANCY, user,
            f"Вагон {wagon.number}: расхождение {percent}% превышает "
            f"допустимые {allowed}%",
            discrepancy_percent=str(percent))
    return wagon


@transaction.atomic
def resolve_discrepancy(wagon: Wagon, action: str, user, reason: str = "") -> Wagon:
    if wagon.status != st.WEIGHT_DISCREPANCY:
        raise _error("У вагона нет расхождения", "no_discrepancy")
    if action == "confirm":
        if not reason:
            raise _error("Укажите обоснование подтверждения фактического веса",
                         "reason_required")
        _set_status(wagon, st.TARE_WEIGHED, user,
                    f"Расхождение по вагону {wagon.number} подтверждено: {reason}",
                    resolution="confirmed", reason=reason)
    elif action == "reweigh":
        _set_status(wagon, st.REWEIGHING_REQUIRED, user,
                    f"Вагон {wagon.number} отправлен на повторное взвешивание",
                    resolution="reweigh")
    else:
        raise _error("Неизвестное действие по расхождению", "bad_resolution")
    return wagon


# ── Оприходование ──────────────────────────────────────────────────────────

def _apply_income(silo: Silo, amount_kg: int, wagon: Wagon, user,
                  measurement_source: str):
    """Записать приход в силос; вызывать только под select_for_update."""
    balance = silo.current_balance_kg
    if balance + amount_kg > silo.total_capacity_kg:
        raise _error(
            f"Приход {amount_kg} кг переполнит силос «{silo.name}»",
            "silo_overflow",
        )
    GrainMovement.objects.create(
        silo=silo, movement_type="income", delta_kg=amount_kg,
        balance_after_kg=balance + amount_kg,
        wagon=wagon, supply=wagon.supply,
        batch_number=f"WAGON-{wagon.pk}",
        note=f"Приход из вагона {wagon.number}",
        created_by=user,
    )
    SiloAllocation.objects.create(
        wagon=wagon, silo=silo, amount_kg=amount_kg,
        measurement_source=measurement_source, operator=user,
    )


@transaction.atomic
def inventory_wagon(wagon: Wagon, user,
                    allocations: list[dict] | None = None) -> Wagon:
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
        parts = [{
            "silo_id": wagon.assigned_silo_id,
            "amount_kg": wagon.net_weight_kg,
            "measurement_source": "manual",
        }]

    for part in parts:
        silo = Silo.objects.select_for_update().get(pk=part["silo_id"])
        _apply_income(
            silo, int(part["amount_kg"]), wagon, user,
            str(part.get("measurement_source") or "manual"),
        )

    reservation = getattr(wagon, "reservation", None)
    if reservation and reservation.active:
        reservation.active = False
        reservation.save(update_fields=["active"])

    _set_status(wagon, st.INVENTORIED, user,
                f"Вагон {wagon.number} оприходован: {wagon.net_weight_kg} кг")
    _set_status(wagon, st.EXIT_ALLOWED, user,
                f"Вагону {wagon.number} разрешён выезд")
    return wagon


# ── Выезд ──────────────────────────────────────────────────────────────────

@transaction.atomic
def register_exit(wagon: Wagon, user, note: str = "") -> Wagon:
    ensure_transition(wagon, st.EXITED)
    wagon.exited_at = timezone.now()
    wagon.exit_note = note
    wagon.save(update_fields=["exited_at", "exit_note"])
    _set_status(wagon, st.EXITED, user, f"Вагон {wagon.number} выехал")
    _set_status(wagon, st.COMPLETED, user,
                f"Цикл вагона {wagon.number} завершён")
    supply = wagon.supply
    if supply and not supply.wagons.exclude(
        status__in=st.TERMINAL_STATUSES,
    ).exists():
        supply.status = "closed"
        supply.save(update_fields=["status"])
    return wagon


# ── Корректировки остатка ──────────────────────────────────────────────────

@transaction.atomic
def adjust_silo(silo: Silo, delta_kg: int, movement_type: str, note: str,
                user) -> GrainMovement:
    if movement_type not in ("adjustment", "inventory_correction",
                             "expense", "transfer_in", "transfer_out"):
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
        raise _error("Остаток силоса не может стать отрицательным",
                     "negative_balance")
    if new_balance > silo.total_capacity_kg:
        raise _error("Операция переполнит силос", "silo_overflow")
    movement = GrainMovement.objects.create(
        silo=silo, movement_type=movement_type, delta_kg=delta_kg,
        balance_after_kg=new_balance, note=note, created_by=user,
    )
    log_event(
        "grain_adjust",
        f"Силос «{silo.name}»: {movement_type} {delta_kg:+} кг ({note})",
        user=user,
        payload={"silo_id": silo.pk, "delta_kg": delta_kg,
                 "movement_type": movement_type},
    )
    return movement
