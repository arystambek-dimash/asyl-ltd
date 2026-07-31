"""Полный цикл вагона и краевые сценарии из ТЗ (сценарии 1–15)."""
import pytest

from apps.grain import services
from apps.grain import statuses as st
from apps.grain.models import (
    GrainMovement, GrainSettings, GrainSupply, Silo, SiloType, Wagon,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def grain_user(user_with_perms):
    return user_with_perms("grain", codes=[
        "grain.view", "grain.supply", "grain.arrive", "grain.weigh",
        "grain.lab", "grain.dispatch", "grain.unload", "grain.inventory",
        "grain.exit", "grain.admin",
    ])


def _supply(**kwargs):
    return GrainSupply.objects.create(**{
        "supplier": "ТОО Колос", "culture": "пшеница", "grain_class": "3",
        "status": "expected", **kwargs,
    })


def _silo(**kwargs):
    return Silo.objects.create(**{
        "name": kwargs.pop("name", "Силос-1"),
        "total_capacity_kg": kwargs.pop("total_capacity_kg", 1_000_000),
        "grain_culture": kwargs.pop("grain_culture", "пшеница"),
        "grain_class": kwargs.pop("grain_class", "3"),
        **kwargs,
    })


def _wagon(supply, number="94120001", **kwargs):
    return Wagon.objects.create(
        supply=supply, number=number, status=st.EXPECTED, **kwargs)


def _drive_to_tare(wagon, user, silo, gross=91_500, tare=23_200):
    services.register_arrival(wagon.number, user)
    wagon.refresh_from_db()
    services.record_gross(wagon, gross, user, source="auto")
    services.record_lab_check(wagon, "accepted", user)
    services.assign_silo(wagon, silo, user, expected_kg=gross - tare)
    services.start_unloading(wagon, user)
    services.finish_unloading(wagon, user)
    services.record_tare(wagon, tare, user, source="auto")
    wagon.refresh_from_db()
    return wagon


# 1. Обычный вагон проходит полный цикл.
def test_full_cycle(grain_user):
    silo = _silo()
    wagon = _wagon(_supply(), document_weight_kg=68_300)
    wagon = _drive_to_tare(wagon, grain_user, silo)

    assert wagon.net_weight_kg == 68_300
    assert wagon.status == st.TARE_WEIGHED

    services.inventory_wagon(wagon, grain_user)
    wagon.refresh_from_db()
    assert wagon.status == st.EXIT_ALLOWED
    assert silo.current_balance_kg == 68_300
    assert silo.reserved_kg == 0  # резерв снят при оприходовании

    services.register_exit(wagon, grain_user)
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    wagon.supply.refresh_from_db()
    assert wagon.supply.status == "closed"


# 2. Номер вагона заранее неизвестен: добавляется к поставке при прибытии.
def test_unknown_number_attaches_to_supply(grain_user):
    supply = _supply()
    wagon = services.register_arrival("94129999", grain_user, supply=supply)
    assert wagon.supply_id == supply.pk
    assert wagon.status == st.ARRIVED


# 3. Вагон без заявки — незапланированный, до подтверждения не разгружается.
def test_unplanned_arrival_requires_approval(grain_user):
    wagon = services.register_arrival("94125555", grain_user)
    assert wagon.unplanned is True
    assert wagon.status == st.WAITING_FOR_APPROVAL

    with pytest.raises(Exception):
        services.record_gross(wagon, 90_000, grain_user, source="auto")

    services.approve_unplanned(wagon, grain_user, supply=_supply())
    wagon.refresh_from_db()
    assert wagon.status == st.ARRIVED


# 4. Повторная регистрация одного вагона на территории запрещена.
def test_duplicate_registration_rejected(grain_user):
    wagon = _wagon(_supply())
    services.register_arrival(wagon.number, grain_user)
    with pytest.raises(Exception) as err:
        services.register_arrival(wagon.number, grain_user)
    assert "wagon_already_on_site" in str(err.value)


# 5. Лаборатория отклонила зерно — разгрузка запрещена.
def test_lab_rejection_blocks_unloading(grain_user):
    wagon = _wagon(_supply())
    services.register_arrival(wagon.number, grain_user)
    wagon.refresh_from_db()
    services.record_gross(wagon, 90_000, grain_user, source="auto")
    services.record_lab_check(wagon, "rejected", grain_user)
    wagon.refresh_from_db()
    assert wagon.status == st.REJECTED
    with pytest.raises(Exception):
        services.assign_silo(wagon, _silo(), grain_user, expected_kg=1000)


# Карантин: только карантинный силос.
def test_quarantine_requires_quarantine_silo(grain_user):
    wagon = _wagon(_supply())
    services.register_arrival(wagon.number, grain_user)
    wagon.refresh_from_db()
    services.record_gross(wagon, 90_000, grain_user, source="auto")
    services.record_lab_check(wagon, "quarantine", grain_user)
    wagon.refresh_from_db()

    ordinary = _silo(name="Обычный")
    with pytest.raises(Exception):
        services.assign_silo(wagon, ordinary, grain_user, expected_kg=1000)

    quarantine = _silo(name="Карантин", is_quarantine=True)
    services.assign_silo(wagon, quarantine, grain_user, expected_kg=1000)
    wagon.refresh_from_db()
    assert wagon.assigned_silo_id == quarantine.pk


# 6. В силосе недостаточно места.
def test_insufficient_capacity(grain_user):
    small = _silo(name="Мелкий", total_capacity_kg=10_000)
    wagon = _wagon(_supply(), expected_weight_kg=68_000)
    services.register_arrival(wagon.number, grain_user)
    wagon.refresh_from_db()
    services.record_gross(wagon, 90_000, grain_user, source="auto")
    services.record_lab_check(wagon, "accepted", grain_user)
    with pytest.raises(Exception) as err:
        services.assign_silo(wagon, small, grain_user)
    assert "insufficient_capacity" in str(err.value)
    wagon.refresh_from_db()
    assert wagon.status == st.INSUFFICIENT_CAPACITY


# 7. Два вагона не могут занять одно место: резерв учитывается сразу.
def test_two_wagons_reserve_same_silo(grain_user):
    silo = _silo(total_capacity_kg=100_000)
    supply = _supply()
    first = _wagon(supply, number="94120001", expected_weight_kg=60_000)
    second = _wagon(supply, number="94120002", expected_weight_kg=60_000)
    for wagon in (first, second):
        services.register_arrival(wagon.number, grain_user)
        wagon.refresh_from_db()
        services.record_gross(wagon, 90_000, grain_user, source="auto")
        services.record_lab_check(wagon, "accepted", grain_user)

    services.assign_silo(first, silo, grain_user)
    with pytest.raises(Exception) as err:
        services.assign_silo(second, silo, grain_user)
    assert "insufficient_capacity" in str(err.value)


def test_configured_incoming_route_is_suggested_first(grain_user):
    silo_type = SiloType.objects.create(
        name="Пшеница 3 класс",
        grain_culture="пшеница",
        grain_class="3",
    )
    ordinary = _silo(name="Резервный")
    preferred = _silo(name="Основной", silo_type=silo_type)
    silo_type.default_silo = preferred
    silo_type.save(update_fields=["default_silo"])
    wagon = _wagon(_supply(), expected_weight_kg=60_000)

    suggestions = services.suggest_silos(wagon)

    assert [silo.pk for silo in suggestions] == [
        preferred.pk, ordinary.pk]


def test_admin_can_create_silo_type_with_default_route(
    auth_client, grain_user,
):
    silo = _silo(name="Маршрутный", grain_culture="", grain_class="")

    response = auth_client(grain_user).post(
        "/api/grain/silo-types/",
        {
            "name": "Ячмень",
            "grain_culture": "ячмень",
            "grain_class": "2",
            "color": "#B7792B",
            "default_silo": silo.pk,
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["default_silo_name"] == "Маршрутный"
    assert response.data["silo_count"] == 1
    silo.refresh_from_db()
    assert silo.silo_type_id == response.data["id"]
    assert silo.grain_culture == "ячмень"
    assert silo.grain_class == "2"


# 8. Смена силоса во время разгрузки сохраняет историю и пере-резервирует.
def test_silo_change_during_unloading(grain_user):
    first = _silo(name="Первый")
    second = _silo(name="Второй")
    wagon = _wagon(_supply(), expected_weight_kg=50_000)
    services.register_arrival(wagon.number, grain_user)
    wagon.refresh_from_db()
    services.record_gross(wagon, 80_000, grain_user, source="auto")
    services.record_lab_check(wagon, "accepted", grain_user)
    services.assign_silo(wagon, first, grain_user)
    services.start_unloading(wagon, grain_user)

    with pytest.raises(Exception):
        services.change_silo(wagon, second, "", grain_user)  # причина обязательна

    services.change_silo(wagon, second, "линия занята", grain_user)
    wagon.refresh_from_db()
    assert wagon.assigned_silo_id == second.pk
    assert wagon.reservation.silo_id == second.pk
    from apps.eventlog.models import EventLog
    assert EventLog.objects.filter(
        event_type="grain_silo_change", payload__wagon_id=wagon.pk).exists()


# 9. Фактический вес отличается от документов сверх допуска.
def test_weight_discrepancy(grain_user):
    settings_row = GrainSettings.get()
    settings_row.allowed_discrepancy_percent = 1
    settings_row.save()
    silo = _silo()
    wagon = _wagon(_supply(), document_weight_kg=60_000)
    wagon = _drive_to_tare(wagon, grain_user, silo)  # нетто 68 300
    assert wagon.status == st.WEIGHT_DISCREPANCY

    with pytest.raises(Exception):
        services.inventory_wagon(wagon, grain_user)  # оприходовать нельзя? —
        # переход WEIGHT_DISCREPANCY→INVENTORIED разрешён только после
        # подтверждения; без reason resolve не пройдёт.

    services.resolve_discrepancy(
        wagon, "confirm", grain_user, reason="акт сверки №1")
    wagon.refresh_from_db()
    assert wagon.status == st.TARE_WEIGHED
    services.inventory_wagon(wagon, grain_user)
    wagon.refresh_from_db()
    assert wagon.status == st.EXIT_ALLOWED


# 10. Повторное взвешивание после расхождения.
def test_reweighing(grain_user):
    settings_row = GrainSettings.get()
    settings_row.allowed_discrepancy_percent = 1
    settings_row.save()
    silo = _silo()
    wagon = _wagon(_supply(), document_weight_kg=60_000)
    wagon = _drive_to_tare(wagon, grain_user, silo)
    assert wagon.status == st.WEIGHT_DISCREPANCY

    services.resolve_discrepancy(wagon, "reweigh", grain_user)
    wagon.refresh_from_db()
    assert wagon.status == st.REWEIGHING_REQUIRED

    services.record_tare(
        wagon, 31_500, grain_user, source="manual",
        manual_reason="повторное взвешивание")
    wagon.refresh_from_db()
    assert wagon.net_weight_kg == 60_000
    assert wagon.status == st.TARE_WEIGHED
    assert wagon.weighings.filter(kind="tare").count() == 2


# 11. Двойное оприходование запрещено.
def test_double_inventory_forbidden(grain_user):
    silo = _silo()
    wagon = _drive_to_tare(
        _wagon(_supply(), document_weight_kg=68_300), grain_user, silo)
    services.inventory_wagon(wagon, grain_user)
    with pytest.raises(Exception):
        services.inventory_wagon(wagon, grain_user)
    assert GrainMovement.objects.filter(
        wagon=wagon, movement_type="income").count() == 1


# 12. Разгрузка одного вагона в два силоса.
def test_split_between_two_silos(grain_user):
    first = _silo(name="Первый")
    second = _silo(name="Второй")
    wagon = _drive_to_tare(
        _wagon(_supply(), document_weight_kg=68_300), grain_user, first)

    with pytest.raises(Exception):
        services.inventory_wagon(wagon, grain_user, allocations=[
            {"silo_id": first.pk, "amount_kg": 40_000},
            {"silo_id": second.pk, "amount_kg": 20_000},
        ])  # сумма не сходится с нетто

    services.inventory_wagon(wagon, grain_user, allocations=[
        {"silo_id": first.pk, "amount_kg": 40_000,
         "measurement_source": "conveyor_scale"},
        {"silo_id": second.pk, "amount_kg": 28_300},
    ])
    assert first.current_balance_kg == 40_000
    assert second.current_balance_kg == 28_300
    assert wagon.allocations.count() == 2


# 13. Выезд до оприходования запрещён.
def test_exit_before_inventory_forbidden(grain_user):
    silo = _silo()
    wagon = _drive_to_tare(
        _wagon(_supply(), document_weight_kg=68_300), grain_user, silo)
    with pytest.raises(Exception):
        services.register_exit(wagon, grain_user)


# 14. Корректировка остатка — только отдельной операцией, без переполнения.
def test_adjustment_movements(grain_user):
    silo = _silo(total_capacity_kg=100_000)
    services.adjust_silo(silo, 40_000, "adjustment", "инвентаризация", grain_user)
    assert silo.current_balance_kg == 40_000
    with pytest.raises(Exception):
        services.adjust_silo(silo, -50_000, "adjustment", "ошибка", grain_user)
    with pytest.raises(Exception):
        services.adjust_silo(silo, 70_000, "adjustment", "перебор", grain_user)
    movement = GrainMovement.objects.filter(silo=silo).first()
    with pytest.raises(RuntimeError):
        movement.delta_kg = 1
        movement.save()
    with pytest.raises(RuntimeError):
        movement.delete()


# 15. Ролевые ограничения: весовщик не решает за лабораторию.
def test_role_limits(auth_client, user_with_perms):
    weigher = user_with_perms("weigher", codes=["grain.view", "grain.weigh"])
    supply = _supply()
    wagon = _wagon(supply)
    services.register_arrival(wagon.number, weigher)

    ok = auth_client(weigher).post(
        f"/api/grain/wagons/{wagon.id}/gross/",
        {"weight_kg": 90_000, "source": "auto"}, format="json")
    assert ok.status_code == 200

    denied = auth_client(weigher).post(
        f"/api/grain/wagons/{wagon.id}/lab/",
        {"decision": "accepted"}, format="json")
    assert denied.status_code == 403

    lab = user_with_perms("lab", codes=["grain.view", "grain.lab"])
    allowed = auth_client(lab).post(
        f"/api/grain/wagons/{wagon.id}/lab/",
        {"decision": "accepted"}, format="json")
    assert allowed.status_code == 200
