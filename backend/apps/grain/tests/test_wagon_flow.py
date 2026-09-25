"""Цикл короткого прихода вагона и краевые сценарии."""
from unittest.mock import patch

import pytest
from rest_framework.exceptions import ValidationError

from apps.grain import services
from apps.grain import statuses as st
from apps.grain.models import (
    GrainMovement,
    GrainSupply,
    Silo,
    SiloReservation,
    SiloType,
    Wagon,
)
from apps.grain import scale
from apps.grain.tests.factories import scale_reading

pytestmark = pytest.mark.django_db


@pytest.fixture
def grain_user(user_with_perms):
    return user_with_perms("grain", codes=[
        "grain.view", "grain.supply", "grain.arrive", "grain.weigh",
        "grain.inventory", "grain.admin",
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


def _simple_wagon(silo, number="94120001", **kwargs):
    """Рейс короткого прихода, уже прибывший к весам."""
    return Wagon.objects.create(**{
        "supply": _supply(), "number": number, "status": st.ARRIVED,
        "workflow": "simple", "assigned_silo": silo, **kwargs,
    })


def _drive_to_exit(wagon, user, gross=91_500, tare=23_200):
    services.record_simple_entry_weight(wagon, gross, user, source="auto")
    services.record_simple_exit_weight(wagon, tare, user, source="auto")
    wagon.refresh_from_db()
    return wagon


# Номер вагона заранее неизвестен: добавляется к рейсу прихода при прибытии.
def test_arrival_fills_the_prepared_simple_trip(grain_user):
    supply = _supply()
    prepared = _wagon(supply, number="", workflow="simple")
    wagon = services.register_arrival("94129999", grain_user, supply=supply)
    assert wagon.pk == prepared.pk
    assert wagon.number == "94129999"
    assert wagon.status == st.ARRIVED


# Прибытие без ожидаемого прихода не принимается.
def test_arrival_requires_a_supply(grain_user):
    with pytest.raises(ValidationError) as err:
        services.register_arrival("94125555", grain_user, supply=None)
    assert err.value.detail["code"] == "supply_required"
    assert not Wagon.objects.exists()


# Повторная регистрация одного вагона на территории запрещена.
def test_duplicate_registration_rejected(grain_user):
    first, second = _supply(), _supply()
    _wagon(first, number="", workflow="simple")
    _wagon(second, number="", workflow="simple")
    services.register_arrival("94120001", grain_user, supply=first)
    with pytest.raises(ValidationError) as err:
        services.register_arrival("94120001", grain_user, supply=second)
    assert err.value.detail["code"] == "wagon_already_on_site"


def _routed_supply(silo_type, **kwargs):
    return _supply(grain_type=silo_type, culture=silo_type.name, grain_class="", **kwargs)


def test_configured_incoming_route_is_the_default_silo(grain_user):
    # Тип зерна из UI: legacy-строк culture/class нет, у поставки culture = название типа.
    silo_type = SiloType.objects.create(name="Пшеница 3 класс")
    _silo(name="Резервный", grain_culture="", grain_class="", total_capacity_kg=5_000_000)
    preferred = _silo(
        name="Основной", silo_type=silo_type, grain_culture="", grain_class="",
    )
    silo_type.default_silo = preferred
    silo_type.save(update_fields=["default_silo"])
    wagon = _wagon(_routed_supply(silo_type), expected_weight_kg=60_000)

    assert services.default_route_silo(wagon) == preferred


def test_default_route_counts_reserved_capacity(grain_user):
    silo_type = SiloType.objects.create(name="Пшеница 3 класс")
    route = _silo(name="Основной", silo_type=silo_type, total_capacity_kg=500_000)
    silo_type.default_silo = route
    silo_type.save(update_fields=["default_silo"])
    services.adjust_silo(route, 300_000, "adjustment", "Начальный остаток", grain_user)
    SiloReservation.objects.create(
        wagon=_wagon(_supply(), number="94120099"), silo=route, amount_kg=100_000)
    supply = _routed_supply(silo_type)

    assert services.default_route_silo(_wagon(supply, expected_weight_kg=100_000)) == route
    assert services.default_route_silo(
        _wagon(supply, number="94120002", expected_weight_kg=100_001)) is None


@pytest.mark.parametrize("unsuitable", [
    {"status": "maintenance"},
    {"is_quarantine": True},
])
def test_unavailable_default_route_is_not_assigned(grain_user, unsuitable):
    silo_type = SiloType.objects.create(name="Пшеница 3 класс")
    route = _silo(name="Основной", silo_type=silo_type, **unsuitable)
    silo_type.default_silo = route
    silo_type.save(update_fields=["default_silo"])
    wagon = _wagon(_routed_supply(silo_type), expected_weight_kg=60_000)

    assert services.default_route_silo(wagon) is None
    assert services.assign_default_silo(wagon) is None


def test_default_route_compatibility_follows_grain_type(grain_user):
    # Тип после миграции 0003: строка культуры силоса не равна названию типа,
    # которое сериализатор пишет в supply.culture, — совместимость решает тип.
    migrated = SiloType.objects.create(
        name="пшеница · 3 класс", grain_culture="пшеница", grain_class="3",
    )
    own = _silo(name="Пшеничный", silo_type=migrated)
    migrated.default_silo = own
    migrated.save(update_fields=["default_silo"])
    wagon = _wagon(_routed_supply(migrated), expected_weight_kg=60_000)
    assert services.default_route_silo(wagon) == own

    barley = SiloType.objects.create(name="Ячмень")
    foreign = _silo(
        name="Ячменный", silo_type=barley, grain_culture="", grain_class="",
    )
    migrated.default_silo = foreign
    migrated.save(update_fields=["default_silo"])
    wagon.supply.grain_type.refresh_from_db()
    assert services.default_route_silo(wagon) is None


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


# Фактический вес отличается от документов сверх допуска: приход ждёт решения.
def test_weight_discrepancy_waits_for_confirmation(grain_user):
    silo = _silo()
    wagon = _drive_to_exit(
        _simple_wagon(silo, document_weight_kg=60_000), grain_user)  # нетто 68 300
    assert wagon.status == st.WEIGHT_DISCREPANCY

    with pytest.raises(ValidationError) as err:
        services.inventory_wagon(wagon, grain_user)
    assert err.value.detail["code"] == "invalid_wagon_transition"

    services.resolve_simple_discrepancy(
        wagon, "confirm", grain_user, reason="акт сверки №1")
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    assert silo.current_balance_kg == 68_300


# Повторное взвешивание после расхождения.
def test_reweighing(grain_user):
    silo = _silo()
    wagon = _drive_to_exit(
        _simple_wagon(silo, document_weight_kg=60_000), grain_user)
    assert wagon.status == st.WEIGHT_DISCREPANCY

    services.resolve_simple_discrepancy(wagon, "reweigh", grain_user)
    wagon.refresh_from_db()
    assert wagon.status == st.AT_SILO
    assert wagon.tare_weight_kg is None

    services.record_simple_exit_weight(
        wagon, 31_500, grain_user, source="manual",
        manual_reason="повторное взвешивание")
    wagon.refresh_from_db()
    assert wagon.net_weight_kg == 60_000
    assert wagon.status == st.COMPLETED
    assert wagon.weighings.filter(kind="tare").count() == 2


# Граница допуска: статус «Расхождение веса» и флаг weight_matches в API
# считаются одним правилом (Wagon.weight_*) и не расходятся на сотых.
@pytest.mark.parametrize(
    ("net_kg", "status", "matches", "percent"),
    [
        (20_101, st.COMPLETED, True, 0.5),  # 0.505 % — как в журнале
        (20_201, st.COMPLETED, True, 1.0),  # 1.005 % → 1.00 — в допуске
        (20_202, st.WEIGHT_DISCREPANCY, False, 1.01),
    ],
)
def test_weight_tolerance_boundary_matches_api(
    grain_user, net_kg, status, matches, percent,
):
    from apps.grain.serializers import WagonSerializer

    wagon = _drive_to_exit(
        _simple_wagon(_silo(), document_weight_kg=20_000), grain_user,
        gross=23_000 + net_kg, tare=23_000)

    assert wagon.status == status
    data = WagonSerializer(wagon).data
    assert data["weight_difference_kg"] == net_kg - 20_000
    assert data["weight_difference_percent"] == percent
    assert data["weight_matches"] is matches


def test_wagon_list_does_not_query_settings_per_row(grain_user):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.grain.serializers import WagonBriefSerializer

    supply = _supply()
    for index in range(3):
        _wagon(
            supply, number=f"9412000{index}", document_weight_kg=60_000,
            gross_weight_kg=90_000, tare_weight_kg=30_000,
            net_weight_kg=60_000,
        )

    with CaptureQueriesContext(connection) as queries:
        rows = WagonBriefSerializer(
            Wagon.objects.filter(supply=supply), many=True).data

    assert [row["weight_matches"] for row in rows] == [True, True, True]
    assert not any(
        "grain_grainsettings" in query["sql"] for query in queries)


# Двойное оприходование запрещено.
def test_double_inventory_forbidden(grain_user):
    silo = _silo()
    wagon = _drive_to_exit(
        _simple_wagon(silo, document_weight_kg=68_300), grain_user)
    assert wagon.status == st.COMPLETED
    with pytest.raises(ValidationError) as err:
        services.inventory_wagon(wagon, grain_user)
    assert err.value.detail["code"] == "invalid_wagon_transition"
    assert GrainMovement.objects.filter(
        wagon=wagon, movement_type="income").count() == 1


# Выезд до оприходования запрещён.
def test_exit_before_inventory_forbidden(grain_user):
    wagon = _simple_wagon(_silo(), document_weight_kg=60_000)
    wagon = _drive_to_exit(wagon, grain_user)
    assert wagon.status == st.WEIGHT_DISCREPANCY
    with pytest.raises(ValidationError) as err:
        services.register_exit(wagon, grain_user)
    assert err.value.detail["code"] == "invalid_wagon_transition"


# 14. Корректировка остатка — только отдельной операцией, без переполнения.
def test_adjustment_movements(grain_user):
    silo = _silo(total_capacity_kg=100_000)
    services.adjust_silo(silo, 40_000, "adjustment", "инвентаризация", grain_user)
    assert silo.current_balance_kg == 40_000
    with pytest.raises(ValidationError) as err:
        services.adjust_silo(silo, -50_000, "adjustment", "ошибка", grain_user)
    assert err.value.detail["code"] == "negative_balance"
    with pytest.raises(ValidationError) as err:
        services.adjust_silo(silo, 70_000, "adjustment", "перебор", grain_user)
    assert err.value.detail["code"] == "silo_overflow"
    movement = GrainMovement.objects.filter(silo=silo).first()
    with pytest.raises(RuntimeError):
        movement.delta_kg = 1
        movement.save()
    with pytest.raises(RuntimeError):
        movement.delete()


def test_adjustment_rejects_fractional_kg(auth_client, grain_user):
    """Дробные кг не обрезаются молча: 1.5 из JSON раньше становилось 1."""
    silo = _silo(total_capacity_kg=100_000)
    client = auth_client(grain_user)
    url = f"/api/grain/silos/{silo.id}/adjust/"

    rejected = client.post(
        url, {"delta_kg": 1.5, "note": "инвентаризация"}, format="json")
    assert rejected.status_code == 400
    assert rejected.data["code"] == "bad_amount"
    assert not GrainMovement.objects.filter(silo=silo).exists()

    accepted = client.post(
        url, {"delta_kg": 1500.0, "note": "инвентаризация"}, format="json")
    assert accepted.status_code == 201
    assert silo.current_balance_kg == 1500
    assert client.post(
        url, {"delta_kg": "-500", "note": "расход"}, format="json"
    ).status_code == 201
    assert silo.current_balance_kg == 1000


# Ролевые ограничения: весовщик не подтверждает расхождение за кладовщика.
def test_role_limits(auth_client, user_with_perms):
    weigher = user_with_perms("weigher", codes=["grain.view", "grain.weigh"])
    wagon = _simple_wagon(_silo(), document_weight_kg=60_000)

    with patch.object(scale, "read_truck_scale", return_value=scale_reading(90_000)):
        ok = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.id}/entry-weight/", {}, format="json"
        )
    assert ok.status_code == 200
    services.record_simple_exit_weight(wagon, 20_000, weigher, source="auto")

    body = {"action": "confirm", "reason": "акт сверки"}
    url = f"/api/grain/wagons/{wagon.id}/resolve-simple-discrepancy/"
    assert auth_client(weigher).post(url, body, format="json").status_code == 403

    storekeeper = user_with_perms(
        "storekeeper", codes=["grain.view", "grain.inventory"])
    allowed = auth_client(storekeeper).post(url, body, format="json")
    assert allowed.status_code == 200
    assert allowed.data["status"] == st.COMPLETED


def test_silo_list_has_bounded_queries(auth_client, grain_user):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client = auth_client(grain_user)
    _silo(name="One")
    with CaptureQueriesContext(connection) as one:
        assert client.get("/api/grain/silos/").status_code == 200
    for index in range(4):
        _silo(name=f"More {index}")
    with CaptureQueriesContext(connection) as many:
        assert client.get("/api/grain/silos/").status_code == 200
    assert len(many) <= len(one) + 2, (len(one), len(many))


@pytest.mark.django_db(transaction=True)
def test_concurrent_last_exits_close_the_supply(grain_user):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections

    supply = _supply()
    wagons = [
        Wagon.objects.create(supply=supply, number=str(9000 + index), status=st.EXIT_ALLOWED)
        for index in range(2)
    ]
    barrier = Barrier(2)

    def finish(wagon):
        try:
            barrier.wait(timeout=10)
            services.register_exit(wagon, grain_user)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(finish, wagon) for wagon in wagons]:
            future.result(timeout=15)

    supply.refresh_from_db()
    assert supply.status == "closed"
