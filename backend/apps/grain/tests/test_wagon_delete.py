"""Удаление завершённого рейса не должно врать про остаток силоса.

Если просто стереть оприходованный вагон, зерно останется в остатке, но
исчезнет из журнала рейсов — и склад перестанет сходиться. Поэтому приход
откатывается компенсирующим расходом, а неизменяемый леджер сохраняется.
"""

import pytest

from apps.catalog.models import Product  # noqa: F401  (регистрация приложений)
from apps.grain import statuses as st
from apps.grain.models import (
    GrainMovement, GrainSupply, Silo, SiloType, Wagon, WeighingRecord,
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
    for path, weight in (("entry-weight", gross), ("exit-weight", tare)):
        response = auth_client(user).post(
            f"/api/grain/wagons/{wagon_id}/{path}/",
            {"weight_kg": weight, "source": "manual", "manual_reason": "весовая"},
            format="json",
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
    for path, weight in (("entry-weight", entry), ("exit-weight", exit_weight)):
        auth_client(user).post(
            f"/api/grain/wagons/{wagon_id}/{path}/",
            {"weight_kg": weight, "source": "manual", "manual_reason": "весовая"},
            format="json",
        )
    return Wagon.objects.get(pk=wagon_id)


def test_deleting_intake_returns_grain_to_a_truthful_balance(
    auth_client, grain_admin,
):
    wagon, silo = _finished_intake(auth_client, grain_admin)
    assert silo.current_balance_kg == 50_000

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon.pk}/delete/")

    assert response.status_code == 200, response.data
    assert response.data["reverted_kg"] == 50_000
    silo.refresh_from_db()
    assert silo.current_balance_kg == 0, "зерно удалённого рейса не должно висеть в силосе"
    assert not Wagon.objects.filter(pk=wagon.pk).exists()


def test_ledger_survives_the_deleted_wagon(auth_client, grain_admin):
    """Движения неизменяемы: остаются обе записи, у прихода отвязан вагон."""
    wagon, silo = _finished_intake(auth_client, grain_admin)

    auth_client(grain_admin).delete(f"/api/grain/wagons/{wagon.pk}/delete/")

    movements = GrainMovement.objects.filter(silo=silo).order_by("id")
    assert [m.movement_type for m in movements] == ["income", "expense"]
    assert [m.delta_kg for m in movements] == [50_000, -50_000]
    assert movements.last().balance_after_kg == 0
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


def test_unfinished_trip_cannot_be_deleted(auth_client, grain_admin):
    """Машину, физически стоящую на территории, стирать нельзя."""
    created = auth_client(grain_admin).post(
        "/api/grain/wagons/passage/",
        {"number": "555 BBB 02", "cargo_name": "Отруби"},
        format="json",
    )
    wagon_id = created.data["id"]

    response = auth_client(grain_admin).delete(
        f"/api/grain/wagons/{wagon_id}/delete/")

    assert response.status_code == 400
    assert response.data["code"] == "wagon_not_finished"
    assert Wagon.objects.filter(pk=wagon_id).exists()


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
    wagon, _ = _finished_intake(auth_client, admin)

    denied = auth_client(operator).delete(f"/api/grain/wagons/{wagon.pk}/delete/")

    assert denied.status_code == 403
    assert Wagon.objects.filter(pk=wagon.pk).exists()


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
