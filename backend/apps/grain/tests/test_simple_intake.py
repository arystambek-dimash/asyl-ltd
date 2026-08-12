from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.grain import statuses as st
from apps.grain.models import GrainMovement, GrainSupply, Silo, SiloType
from apps.shipments import scale

pytestmark = pytest.mark.django_db


@pytest.fixture
def grain_operator(user_with_perms):
    return user_with_perms(
        "simple-grain",
        codes=[
            "grain.view",
            "grain.supply",
            "grain.arrive",
            "grain.weigh",
            "grain.inventory",
        ],
    )


def _setup_route():
    grain_type = SiloType.objects.create(name="Пшеница продовольственная")
    silo = Silo.objects.create(
        name="Силос-7",
        total_capacity_kg=500_000,
        silo_type=grain_type,
    )
    return grain_type, silo


def _create_intake(auth_client, user, grain_type, silo, expected=68_300):
    response = auth_client(user).post(
        "/api/grain/supplies/",
        {
            "supplier": "ТОО Колос",
            "grain_type": grain_type.pk,
            "assigned_silo": silo.pk,
            "expected_total_kg": expected,
            "simple_flow": True,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    return response.data


def _scale_reading(weight):
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.2"),
        updated_at="2026-08-12T10:00:00Z",
    )


def test_simple_intake_runs_from_camera_to_net_and_closes(auth_client, grain_operator):
    grain_type, silo = _setup_route()
    supply_data = _create_intake(auth_client, grain_operator, grain_type, silo)

    supply = GrainSupply.objects.get(pk=supply_data["id"])
    wagon = supply.wagons.get()
    assert supply.status == "expected"
    assert wagon.number == ""
    assert wagon.workflow == "simple"
    assert wagon.assigned_silo == silo
    assert silo.reserved_kg == 68_300
    awaiting = auth_client(grain_operator).get(
        "/api/grain/supplies/?status=expected&awaiting_arrival=1&page=1"
    )
    assert [row["id"] for row in awaiting.data["results"]] == [supply.pk]

    arrival = auth_client(grain_operator).post(
        "/api/grain/wagons/camera-arrive/",
        {
            "supply": supply.pk,
            "number": "94120077",
            "camera_source": "cam7",
        },
        format="json",
    )
    assert arrival.status_code == 201
    assert arrival.data["number_source"] == "camera"
    assert arrival.data["number_camera_source"] == "cam7"
    wagon.refresh_from_db()
    assert wagon.status == st.ARRIVED
    awaiting = auth_client(grain_operator).get(
        "/api/grain/supplies/?status=expected&awaiting_arrival=1&page=1"
    )
    assert awaiting.data["results"] == []

    with patch.object(
        scale, "read_truck_scale", return_value=_scale_reading(91_500)
    ):
        entry = auth_client(grain_operator).post(
            f"/api/grain/wagons/{wagon.pk}/entry-weight/", {}, format="json"
        )
    assert entry.status_code == 200, entry.data
    assert entry.data["status"] == st.AT_SILO
    assert entry.data["assigned_silo_name"] == "Силос-7"

    with patch.object(
        scale, "read_truck_scale", return_value=_scale_reading(23_200)
    ):
        exit_weight = auth_client(grain_operator).post(
            f"/api/grain/wagons/{wagon.pk}/exit-weight/", {}, format="json"
        )
    assert exit_weight.status_code == 200, exit_weight.data
    assert exit_weight.data["status"] == st.COMPLETED
    assert exit_weight.data["net_weight_kg"] == 68_300
    assert exit_weight.data["weight_matches"] is True
    assert GrainMovement.objects.get(wagon=wagon).delta_kg == 68_300
    supply.refresh_from_db()
    assert supply.status == "closed"


def test_simple_intake_stops_on_weight_difference_until_confirmed(
    auth_client, grain_operator
):
    grain_type, silo = _setup_route()
    supply_data = _create_intake(
        auth_client, grain_operator, grain_type, silo, expected=70_000
    )
    supply = GrainSupply.objects.get(pk=supply_data["id"])
    wagon = supply.wagons.get()
    auth_client(grain_operator).post(
        "/api/grain/wagons/camera-arrive/",
        {"supply": supply.pk, "number": "94120088", "camera_source": "cam7"},
        format="json",
    )
    with patch.object(
        scale, "read_truck_scale", return_value=_scale_reading(91_500)
    ):
        auth_client(grain_operator).post(
            f"/api/grain/wagons/{wagon.pk}/entry-weight/", {}, format="json"
        )

    with patch.object(
        scale, "read_truck_scale", return_value=_scale_reading(23_200)
    ):
        response = auth_client(grain_operator).post(
            f"/api/grain/wagons/{wagon.pk}/exit-weight/", {}, format="json"
        )

    assert response.status_code == 200
    assert response.data["status"] == st.WEIGHT_DISCREPANCY
    assert response.data["weight_matches"] is False
    assert not GrainMovement.objects.filter(wagon=wagon).exists()

    confirmed = auth_client(grain_operator).post(
        f"/api/grain/wagons/{wagon.pk}/resolve-simple-discrepancy/",
        {"action": "confirm", "reason": "Подтверждено актом весовой"},
        format="json",
    )
    assert confirmed.status_code == 200
    assert confirmed.data["status"] == st.COMPLETED


def test_supply_operator_can_create_grain_type(auth_client, grain_operator):
    response = auth_client(grain_operator).post(
        "/api/grain/types/",
        {
            "name": "Ячмень фуражный",
            "color": "#B78132",
            "description": "Фураж",
        },
        format="json",
    )

    assert response.status_code == 201


def test_simple_intake_requires_type_weight_and_silo(auth_client, grain_operator):
    response = auth_client(grain_operator).post(
        "/api/grain/supplies/",
        {"supplier": "ТОО Колос", "simple_flow": True},
        format="json",
    )

    assert response.status_code == 400
    assert set(response.data["detail"]) >= {
        "grain_type",
        "assigned_silo",
        "expected_total_kg",
    }
