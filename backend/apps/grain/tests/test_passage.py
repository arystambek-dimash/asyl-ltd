"""Проход: вывоз отрубей. Машина въезжает пустой и уезжает гружёной.

Ключевое отличие от прихода — обратная формула нетто. У прихода транспорт
приезжает с грузом (нетто = въезд − выезд), у прохода увозит груз
(нетто = выезд − въезд). Ошибка знака здесь тихо испортит учёт вывоза,
поэтому направление проверяется отдельно от общего потока.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.grain import statuses as st
from apps.grain.models import GrainMovement, Wagon
from apps.grain import scale

pytestmark = pytest.mark.django_db


@pytest.fixture
def gate_operator(user_with_perms):
    return user_with_perms(
        "passage-operator",
        codes=["grain.view", "grain.arrive", "grain.weigh", "grain.exit"],
    )


def _open_passage(auth_client, user, cargo="Отруби", number="123 ABC 02"):
    response = auth_client(user).post(
        "/api/grain/wagons/passage/",
        {"number": number, "cargo_name": cargo},
        format="json",
    )
    assert response.status_code == 201, response.data
    return response.data


def _weigh(auth_client, user, wagon_id, path, weight):
    reading = scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.2"),
        updated_at="2026-08-12T10:00:00Z",
    )
    with patch.object(scale, "read_truck_scale", return_value=reading):
        return auth_client(user).post(
            f"/api/grain/wagons/{wagon_id}/{path}/",
            {},
            format="json",
        )


def test_passage_net_is_exit_minus_entry(auth_client, gate_operator):
    """Полный цикл: заехал пустым 12 т, уехал гружёным 30 т → вывезено 18 т."""
    passage = _open_passage(auth_client, gate_operator)
    assert passage["direction"] == "passage"
    assert passage["cargo_name"] == "Отруби"
    assert passage["status"] == st.ARRIVED

    entry = _weigh(auth_client, gate_operator, passage["id"], "entry-weight", 12_000)
    assert entry.status_code == 200, entry.data
    assert entry.data["entry_weight_kg"] == 12_000
    assert entry.data["net_weight_kg"] is None, "нетто до выезда неизвестно"

    exit_response = _weigh(
        auth_client, gate_operator, passage["id"], "exit-weight", 30_000)
    assert exit_response.status_code == 200, exit_response.data
    assert exit_response.data["entry_weight_kg"] == 12_000
    assert exit_response.data["exit_weight_kg"] == 30_000
    # Обратная приходу формула: увезли разницу, а не оставили её.
    assert exit_response.data["net_weight_kg"] == 18_000
    assert exit_response.data["status"] == st.COMPLETED


def test_passage_rejects_exit_lighter_than_entry(auth_client, gate_operator):
    """Гружёная машина не может быть легче пустой — это ошибка весовой."""
    passage = _open_passage(auth_client, gate_operator)
    _weigh(auth_client, gate_operator, passage["id"], "entry-weight", 20_000)

    response = _weigh(
        auth_client, gate_operator, passage["id"], "exit-weight", 19_000)

    assert response.status_code == 400
    assert response.data["code"] == "bad_exit_weight"


def test_passage_exit_needs_entry_weight_first(auth_client, gate_operator):
    passage = _open_passage(auth_client, gate_operator)

    response = _weigh(
        auth_client, gate_operator, passage["id"], "exit-weight", 30_000)

    assert response.status_code == 400


def test_passage_requires_cargo_name(auth_client, gate_operator):
    response = auth_client(gate_operator).post(
        "/api/grain/wagons/passage/",
        {"number": "777 AAA 02", "cargo_name": "  "},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "cargo_required"


def test_passage_does_not_touch_silo_stock(auth_client, gate_operator):
    """Вывоз не оприходуется в силос: остатки зерна он не меняет."""
    passage = _open_passage(auth_client, gate_operator)
    _weigh(auth_client, gate_operator, passage["id"], "entry-weight", 10_000)
    _weigh(auth_client, gate_operator, passage["id"], "exit-weight", 25_000)

    wagon = Wagon.objects.get(pk=passage["id"])
    assert wagon.assigned_silo_id is None
    assert not GrainMovement.objects.filter(wagon=wagon).exists()


def test_passage_list_is_filterable_by_direction(auth_client, gate_operator):
    _open_passage(auth_client, gate_operator, number="111 AAA 02")

    passages = auth_client(gate_operator).get(
        "/api/grain/wagons/?direction=passage")
    intakes = auth_client(gate_operator).get(
        "/api/grain/wagons/?direction=intake")

    assert passages.status_code == 200
    assert [row["number"] for row in passages.data] == ["111 AAA 02"]
    assert all(row["direction"] == "intake" for row in intakes.data)


def test_intake_net_stays_entry_minus_exit(auth_client, gate_operator):
    """Регрессия: приход считает нетто по-старому, направление его не ломает."""
    wagon = Wagon.objects.create(
        direction=Wagon.INTAKE,
        gross_weight_kg=68_000,
        tare_weight_kg=20_000,
    )
    assert wagon.computed_net_kg() == 48_000

    outbound = Wagon.objects.create(
        direction=Wagon.PASSAGE,
        gross_weight_kg=20_000,
        tare_weight_kg=68_000,
    )
    assert outbound.computed_net_kg() == 48_000
