"""Удаление рейса не должно врать про остаток и физический процесс.

Если просто стереть оприходованный вагон, зерно останется в остатке, но
исчезнет из журнала рейсов — и склад перестанет сходиться. Поэтому приход
откатывается компенсирующим расходом, а неизменяемый леджер сохраняется.
Активный рейс удаляется только сильным правом и с обязательной причиной.
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from decimal import Decimal
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product  # noqa: F401  (регистрация приложений)
from apps.eventlog.models import EventLog
from apps.grain import passage_scale_automation as automation
from apps.grain import scale, services
from apps.grain import statuses as st
from apps.grain.models import (
    AutomaticPassageCapture,
    GrainMovement,
    GrainSupply,
    PassageScaleAutomationState,
    PassageWeightCapture,
    Silo,
    SiloReservation,
    SiloType,
    Wagon,
    WeighingRecord,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def grain_admin(user_with_perms):
    return user_with_perms(
        "grain-delete",
        codes=[
            "grain.view", "grain.supply", "grain.arrive", "grain.weigh",
            "grain.inventory", "grain.exit", "grain.delete",
        ],
    )


def _reading(weight):
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.2"),
        updated_at="2026-08-12T10:00:00Z",
    )


def _finished_intake(auth_client, user, *, expected=50_000, gross=70_000, tare=20_000):
    """Короткий приход, доведённый до завершения: 50 т легли в силос."""
    grain_type = SiloType.objects.create(name=f"Тип-{SiloType.objects.count() + 1}")
    silo = Silo.objects.create(
        name=f"Силос-{Silo.objects.count() + 1}",
        total_capacity_kg=500_000,
        silo_type=grain_type,
    )
    created = auth_client(user).post(
        "/api/grain/supplies/",
        {
            "supplier": "ТОО Колос", "grain_type": grain_type.pk,
            "assigned_silo": silo.pk, "expected_total_kg": expected,
            "simple_flow": True,
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    wagon = Wagon.objects.get(supply_id=created.data["id"])
    arrive = auth_client(user).post(
        "/api/grain/wagons/arrive/",
        {"number": f"W-{wagon.pk}", "supply": created.data["id"]},
        format="json",
    )
    assert arrive.status_code == 201, arrive.data
    wagon_id = arrive.data["id"]
    with patch.object(
        scale,
        "read_truck_scale",
        side_effect=[_reading(gross), _reading(tare)],
    ):
        for path in ("entry-weight", "exit-weight"):
            response = auth_client(user).post(
                f"/api/grain/wagons/{wagon_id}/{path}/", {}, format="json"
            )
            assert response.status_code == 200, response.data
    return Wagon.objects.get(pk=wagon_id), silo


def _passage(auth_client, user, *, entry=12_000, exit_weight=30_000):
    created = auth_client(user).post(
        "/api/grain/wagons/passage/",
        {"number": "777 AAA 02", "cargo_name": "Отруби"},
        format="json",
    )
    assert created.status_code == 201, created.data
    wagon_id = created.data["id"]
    with patch.object(
        scale,
        "read_truck_scale",
        side_effect=[_reading(entry), _reading(exit_weight)],
    ):
        for path in ("entry-weight", "exit-weight"):
            auth_client(user).post(
                f"/api/grain/wagons/{wagon_id}/{path}/", {}, format="json"
            )
    return Wagon.objects.get(pk=wagon_id)


def _active_intake_at_unloading_completed(user, *, amount=50_000):
    supply = GrainSupply.objects.create(
        supplier="ТОО Активный приход",
        culture="пшеница",
        grain_class="3",
        status="expected",
    )
    silo = Silo.objects.create(
        name=f"Активный силос-{Silo.objects.count() + 1}",
        total_capacity_kg=500_000,
        grain_culture="пшеница",
        grain_class="3",
    )
    wagon = Wagon.objects.create(
        supply=supply,
        number="ACTIVE-INTAKE",
        status=st.EXPECTED,
        expected_weight_kg=amount,
    )
    services.register_arrival(wagon.number, user)
    wagon.refresh_from_db()
    services.record_gross(wagon, amount + 20_000, user, source="auto")
    services.record_lab_check(wagon, "accepted", user)
    services.assign_silo(wagon, silo, user, expected_kg=amount)
    services.start_unloading(wagon, user)
    services.finish_unloading(wagon, user)
    wagon.refresh_from_db()
    return wagon, silo


def test_deleting_intake_returns_grain_to_a_truthful_balance(
    auth_client, grain_admin,
):
    wagon, silo = _finished_intake(auth_client, grain_admin)
    assert silo.current_balance_kg == 50_000

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/")

    assert response.status_code == 200, response.data
    assert response.data["reverted_kg"] == 50_000
    assert response.data["released_reservation_kg"] == 0
    silo.refresh_from_db()
    assert silo.current_balance_kg == 0, (
        "зерно удалённого рейса не должно висеть в силосе"
    )
    assert not Wagon.objects.filter(pk=wagon.pk).exists()


def test_ledger_survives_the_deleted_wagon(auth_client, grain_admin):
    """Движения неизменяемы: остаются обе записи, у прихода отвязан вагон."""
    wagon, silo = _finished_intake(auth_client, grain_admin)

    auth_client(grain_admin).delete(f"/api/grain/wagons/{wagon.pk}/delete/")

    movements = GrainMovement.objects.filter(silo=silo).order_by("id")
    assert [m.movement_type for m in movements] == ["income", "expense"]
    assert [m.delta_kg for m in movements] == [50_000, -50_000]
    assert movements.last().balance_after_kg == 0
    assert movements.last().supply_id == wagon.supply_id
    assert movements.last().batch_number == f"DELETE-WAGON-{wagon.pk}"
    assert all(m.wagon_id is None for m in movements)


def test_deleting_passage_touches_no_silo(auth_client, grain_admin):
    """У вывоза силоса нет — откатывать нечего, удаление просто проходит."""
    wagon = _passage(auth_client, grain_admin)
    before = GrainMovement.objects.count()

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/")

    assert response.status_code == 200, response.data
    assert response.data["reverted_kg"] == 0
    assert GrainMovement.objects.count() == before
    assert not Wagon.objects.filter(pk=wagon.pk).exists()


def test_active_passage_requires_reason_then_can_be_deleted(
    auth_client,
    grain_admin,
):
    created = auth_client(grain_admin).post(
        "/api/grain/wagons/passage/",
        {"number": "555 BBB 02", "cargo_name": "Отруби"},
        format="json",
    )
    wagon_id = created.data["id"]

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon_id}/delete/")

    assert response.status_code == 400
    assert response.data["code"] == "delete_reason_required"
    assert Wagon.objects.filter(pk=wagon_id).exists()

    deleted = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon_id}/delete/",
        {"reason": "  Ошибочно   зарегистрирован  "},
        format="json",
    )

    assert deleted.status_code == 200, deleted.data
    assert deleted.data == {
        "reverted_kg": 0,
        "released_reservation_kg": 0,
    }
    assert not Wagon.objects.filter(pk=wagon_id).exists()
    event = EventLog.objects.get(
        event_type="grain_wagon_deleted",
        payload__wagon_id=wagon_id,
    )
    assert event.payload["status"] == st.ARRIVED
    assert event.payload["active_deletion"] is True
    assert event.payload["reason"] == "Ошибочно зарегистрирован"


def test_processing_weight_capture_blocks_wagon_deletion(
    auth_client,
    grain_admin,
):
    wagon = Wagon.objects.create(
        number="555BBB02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
        arrived_at=timezone.now(),
    )
    capture = PassageWeightCapture.objects.create(
        idempotency_key="4fbd9ed6-0c61-4a2e-8d14-dac48fef4cbe",
        wagon=wagon,
        wagon_id_snapshot=wagon.pk,
        action=PassageWeightCapture.ENTRY,
        wagon_status_before=wagon.status,
        camera="cam1",
    )

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/",
        {"reason": "Ошибочно зарегистрирован"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "passage_capture_in_progress"
    assert Wagon.objects.filter(pk=wagon.pk).exists()
    capture.refresh_from_db()
    assert capture.wagon_id == wagon.pk


def test_unresolved_automatic_capture_blocks_active_passage_deletion(
    auth_client,
    grain_admin,
):
    wagon = Wagon.objects.create(
        number="555BBB02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        arrived_at=timezone.now(),
        gross_weight_kg=12_000,
        number_source="camera",
    )
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key="8858f757-7e90-4ca2-924f-3ce701912a42",
        camera="cam1",
        weight_kg=30_000,
        status=AutomaticPassageCapture.PROCESSING,
        stage=AutomaticPassageCapture.RECOGNIZING,
    )

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/",
        {"reason": "Ошибочно зарегистрирован"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "passage_capture_in_progress"
    assert Wagon.objects.filter(pk=wagon.pk).exists()
    capture.refresh_from_db()
    assert capture.status == AutomaticPassageCapture.PROCESSING


@pytest.mark.django_db(transaction=True)
def test_episode_claim_and_passage_deletion_share_lane_mutex(
    grain_admin,
    settings,
):
    if connection.vendor != "postgresql":
        pytest.skip("row-lock contract requires PostgreSQL")
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    state, _created = PassageScaleAutomationState.objects.update_or_create(
        scale_number=scale.TRUCK_SCALE_KEY,
        defaults={"phase": PassageScaleAutomationState.ARMED},
    )
    wagon = Wagon.objects.create(
        number="555BBB02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        arrived_at=timezone.now(),
        gross_weight_kg=12_000,
        number_source="camera",
    )
    started = Event()

    def delete_during_claim():
        close_old_connections()
        started.set()
        try:
            services.delete_wagon(
                Wagon.objects.get(pk=wagon.pk),
                type(grain_admin).objects.get(pk=grain_admin.pk),
                reason="Ошибочно зарегистрирован",
            )
        except ValidationError as exc:
            return str(exc.detail["code"])
        finally:
            connections.close_all()
        return "deleted"

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with transaction.atomic():
            locked_state = PassageScaleAutomationState.objects.select_for_update().get(
                pk=state.pk
            )
            capture = AutomaticPassageCapture.objects.create(
                idempotency_key="bdca1a8d-a62b-4410-a093-6cf2cf1dbf63",
                camera="cam1",
            )
            locked_state.phase = PassageScaleAutomationState.PROCESSING
            locked_state.current_capture = capture
            locked_state.save(update_fields=["phase", "current_capture", "updated_at"])
            # Simulate a rolling deploy where this web process has the flag off
            # while the old monitor has already claimed the durable lane.
            settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
            future = pool.submit(delete_during_claim)
            assert started.wait(timeout=5)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.25)
        assert future.result(timeout=5) == "passage_capture_in_progress"
    finally:
        pool.shutdown(wait=True)

    assert Wagon.objects.filter(pk=wagon.pk).exists()


def test_successful_passage_deletion_disarms_previously_observed_lane(
    auth_client,
    grain_admin,
    settings,
):
    """An occupied snapshot from before DELETE must not start a new trip."""

    wagon = _passage(auth_client, grain_admin)
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG = 500
    settings.VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS = 2
    PassageScaleAutomationState.objects.update_or_create(
        scale_number=scale.TRUCK_SCALE_KEY,
        defaults={"phase": PassageScaleAutomationState.ARMED},
    )
    occupied_before_delete = scale.ScaleObservation(
        state="ready",
        weight_kg=Decimal(30000),
        connected=True,
        stable=True,
        stale=False,
        age_seconds=Decimal("0.2"),
        updated_at="2026-09-03T07:30:00Z",
    )

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/"
    )
    assert response.status_code == 200, response.data

    work = automation._advance_lane(occupied_before_delete, now=timezone.now())

    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert work is None
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert state.stable_streak == 0
    assert state.candidate_weight_kg is None
    assert state.current_capture_id is None
    assert not AutomaticPassageCapture.objects.exists()

    empty_after_delete = scale.ScaleObservation(
        state="ready",
        weight_kg=Decimal(0),
        connected=True,
        stable=True,
        stale=False,
        age_seconds=Decimal("0.2"),
        updated_at="2026-09-03T07:30:01Z",
    )
    automation._advance_lane(empty_after_delete, now=timezone.now())
    state.refresh_from_db()
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 1

    automation._advance_lane(empty_after_delete, now=timezone.now())
    state.refresh_from_db()
    assert state.phase == PassageScaleAutomationState.ARMED
    assert state.clear_streak == 0


def test_active_intake_delete_releases_reservation_without_changing_stock(
    auth_client,
    grain_admin,
):
    wagon, silo = _active_intake_at_unloading_completed(grain_admin)
    wagon_id = wagon.pk
    assert wagon.status == st.UNLOADING_COMPLETED
    assert silo.current_balance_kg == 0
    assert silo.reserved_kg == 50_000

    missing_confirmation = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon_id}/delete/",
        {"reason": "Дублирующий приход"},
        format="json",
    )

    assert missing_confirmation.status_code == 400
    assert (
        missing_confirmation.data["code"]
        == "unrecorded_grain_confirmation_required"
    )
    assert Wagon.objects.filter(pk=wagon_id).exists()
    assert silo.reserved_kg == 50_000

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon_id}/delete/",
        {
            "reason": "Дублирующий приход",
            "confirm_unrecorded_grain_handled": True,
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["reverted_kg"] == 0
    assert response.data["released_reservation_kg"] == 50_000
    assert not Wagon.objects.filter(pk=wagon_id).exists()
    assert not SiloReservation.objects.filter(wagon_id=wagon_id).exists()
    silo.refresh_from_db()
    assert silo.current_balance_kg == 0
    assert silo.reserved_kg == 0
    assert silo.free_capacity_kg == silo.total_capacity_kg
    event = EventLog.objects.get(
        event_type="grain_wagon_deleted",
        payload__wagon_id=wagon_id,
    )
    assert event.payload["status"] == st.UNLOADING_COMPLETED
    assert event.payload["unrecorded_grain_confirmation_required"] is True
    assert event.payload["confirm_unrecorded_grain_handled"] is True
    assert event.payload["weighing_count"] == 1
    assert event.payload["lab_check_count"] == 1
    assert event.payload["reservation"] == {
        "id": event.payload["reservation"]["id"],
        "silo_id": silo.pk,
        "amount_kg": 50_000,
        "active": True,
    }


@pytest.mark.parametrize("status", sorted(st.ON_SITE_STATUSES))
def test_every_on_site_status_is_deletable_with_reason(
    auth_client,
    grain_admin,
    status,
):
    wagon = Wagon.objects.create(
        number=f"ACTIVE-{status}",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=status,
    )

    payload = {"reason": "Удаление ошибочной записи"}
    if status in {st.UNLOADING, st.UNLOADING_COMPLETED}:
        payload["confirm_unrecorded_grain_handled"] = True
    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/",
        payload,
        format="json",
    )

    assert response.status_code == 200, response.data
    assert not Wagon.objects.filter(pk=wagon.pk).exists()


@pytest.mark.parametrize("status", [st.EXPECTED, st.UNPLANNED])
def test_not_yet_on_site_wagon_cannot_use_emergency_delete(
    auth_client,
    grain_admin,
    status,
):
    wagon = Wagon.objects.create(number=f"NOT-ON-SITE-{status}", status=status)

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/",
        {"reason": "Ошибочная запись"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "wagon_delete_not_allowed"
    assert Wagon.objects.filter(pk=wagon.pk).exists()


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("   ", "delete_reason_required"),
        ("нет", "delete_reason_required"),
        (12345, "bad_delete_reason"),
        ("x" * 201, "delete_reason_too_long"),
    ],
)
def test_active_delete_validates_reason(
    auth_client,
    grain_admin,
    reason,
    code,
):
    wagon = Wagon.objects.create(
        number="BAD-REASON",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
    )

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/",
        {"reason": reason},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == code
    assert Wagon.objects.filter(pk=wagon.pk).exists()


@pytest.mark.parametrize("confirmation", [None, False, 1, "true"])
def test_unloading_intake_requires_literal_true_safety_confirmation(
    auth_client,
    grain_admin,
    confirmation,
):
    wagon = Wagon.objects.create(
        number="UNRECORDED-GRAIN",
        direction=Wagon.INTAKE,
        status=st.UNLOADING,
    )
    payload = {
        "reason": "Исправление ошибочного прихода",
        "confirm_unrecorded_grain_handled": confirmation,
    }

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/",
        payload,
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "unrecorded_grain_confirmation_required"
    assert Wagon.objects.filter(pk=wagon.pk).exists()


def test_delete_requires_the_grain_delete_permission(auth_client, user_with_perms):
    operator = user_with_perms(
        "grain-no-delete",
        codes=["grain.view", "grain.arrive", "grain.weigh"],
    )
    admin = user_with_perms(
        "grain-can-delete",
        codes=[
            "grain.view", "grain.supply", "grain.arrive", "grain.weigh",
            "grain.inventory", "grain.exit", "grain.delete",
        ],
    )
    created = auth_client(admin).post(
        "/api/grain/wagons/passage/",
        {"number": "NO DELETE", "cargo_name": "Отруби"},
        format="json",
    )
    wagon_id = created.data["id"]

    denied = auth_client(operator).delete(
        f"/api/grain/wagons/{wagon_id}/delete/",
        {"reason": "Попытка без разрешения"},
        format="json",
    )

    assert denied.status_code == 403
    assert Wagon.objects.filter(pk=wagon_id).exists()


def test_delete_clears_child_records(auth_client, grain_admin):
    """Взвешивания рейса уходят вместе с ним, иначе FK PROTECT не пустит."""
    wagon, _ = _finished_intake(auth_client, grain_admin)
    assert WeighingRecord.objects.filter(wagon=wagon).exists()

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/")

    assert response.status_code == 200, response.data
    assert not WeighingRecord.objects.filter(wagon_id=wagon.pk).exists()


def test_supply_closes_when_its_last_wagon_is_deleted(auth_client, grain_admin):
    wagon, _ = _finished_intake(auth_client, grain_admin)
    supply_id = wagon.supply_id

    auth_client(grain_admin).delete(f"/api/grain/wagons/{wagon.pk}/delete/")

    assert GrainSupply.objects.get(pk=supply_id).status == "closed"


def test_deleted_trip_disappears_from_the_finished_list(auth_client, grain_admin):
    wagon, _ = _finished_intake(auth_client, grain_admin)
    listed = auth_client(grain_admin).get("/api/grain/wagons/?scope=finished")
    assert any(row["id"] == wagon.pk for row in listed.data)

    auth_client(grain_admin).delete(f"/api/grain/wagons/{wagon.pk}/delete/")

    after = auth_client(grain_admin).get("/api/grain/wagons/?scope=finished")
    assert all(row["id"] != wagon.pk for row in after.data)
    assert wagon.status in st.TERMINAL_STATUSES | {st.EXITED}
