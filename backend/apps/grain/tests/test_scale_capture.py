from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.eventlog.models import EventLog
from apps.grain import services
from apps.grain import statuses as st
from apps.grain.models import GrainSupply, Wagon, WeighingRecord
from apps.grain import scale

pytestmark = pytest.mark.django_db


@pytest.fixture
def weigher(user_with_perms):
    return user_with_perms(
        "grain-scale-weigher",
        codes=["grain.view", "grain.weigh"],
    )


def _reading(weight: str) -> scale.ScaleReading:
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.4"),
        updated_at="2026-08-12T10:00:00Z",
    )


def _arrived_wagon(weigher, **kwargs) -> Wagon:
    supply = GrainSupply.objects.create(
        supplier="ТОО Весы",
        culture="пшеница",
        grain_class="3",
        status="expected",
    )
    wagon = Wagon.objects.create(
        supply=supply,
        number="94129901",
        status=st.EXPECTED,
        **kwargs,
    )
    services.register_arrival(wagon.number, weigher)
    wagon.refresh_from_db()
    return wagon


def test_gross_reads_scale_once_rounds_to_whole_kg_and_records_provenance(
    auth_client,
    weigher,
):
    wagon = _arrived_wagon(weigher)
    with patch.object(
        scale,
        "read_truck_scale",
        return_value=_reading("91500.50"),
    ) as read_scale:
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/gross/", {}, format="json"
        )

    assert response.status_code == 200, response.data
    assert response.data["gross_weight_kg"] == 91_501
    assert read_scale.call_count == 1
    weighing = WeighingRecord.objects.get(wagon=wagon)
    assert weighing.weight_kg == 91_501
    assert weighing.source == "scale"
    assert weighing.manual_reason == ""
    payload = EventLog.objects.filter(
        event_type="grain_weighing",
        payload__wagon_id=wagon.pk,
    ).latest("id").payload
    assert payload["scale_age_seconds"] == "0.4"
    assert payload["scale_updated_at"] == "2026-08-12T10:00:00Z"


@pytest.mark.parametrize(
    ("raw_weight", "expected_status", "expected_weight"),
    [
        ("0.49", 409, None),
        ("0.50", 200, 1),
    ],
)
def test_scale_decimal_boundary_when_storing_integer_kilograms(
    auth_client,
    weigher,
    raw_weight,
    expected_status,
    expected_weight,
):
    wagon = _arrived_wagon(weigher)
    with patch.object(
        scale,
        "read_truck_scale",
        return_value=_reading(raw_weight),
    ):
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/gross/", {}, format="json"
        )

    assert response.status_code == expected_status
    wagon.refresh_from_db()
    assert wagon.gross_weight_kg == expected_weight
    assert wagon.weighings.exists() is (expected_weight is not None)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("gross", {"weight_kg": 91_500}),
        ("tare", {"source": "manual"}),
        ("entry-weight", {"manual_reason": "оператор"}),
        ("exit-weight", {"scale_number": "rail-1"}),
    ],
)
def test_all_weight_actions_reject_client_controlled_measurements(
    auth_client,
    weigher,
    path,
    payload,
):
    wagon = _arrived_wagon(weigher)
    with patch.object(scale, "read_truck_scale") as read_scale:
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/{path}/",
            payload,
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "scale_weight_server_only"
    read_scale.assert_not_called()
    wagon.refresh_from_db()
    assert wagon.status == st.ARRIVED
    assert not wagon.weighings.exists()


@pytest.mark.parametrize("raw_body", ["[]", "false", "0", '""'])
def test_scale_action_requires_an_empty_json_object(
    auth_client,
    weigher,
    raw_body,
):
    wagon = _arrived_wagon(weigher)
    with patch.object(scale, "read_truck_scale") as read_scale:
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/gross/",
            data=raw_body,
            content_type="application/json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "scale_weight_server_only"
    read_scale.assert_not_called()


def test_not_ready_scale_does_not_change_wagon(auth_client, weigher):
    wagon = _arrived_wagon(weigher)
    with patch.object(
        scale,
        "read_truck_scale",
        side_effect=scale.TruckScaleNotReady(),
    ) as read_scale:
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/gross/", {}, format="json"
        )

    assert response.status_code == 409
    assert response.data["code"] == "truck_scale_not_ready"
    assert read_scale.call_count == 1
    wagon.refresh_from_db()
    assert wagon.status == st.ARRIVED
    assert wagon.gross_weight_kg is None
    assert not wagon.weighings.exists()


def test_stale_repeated_action_is_rejected_before_reading_scale(
    auth_client,
    weigher,
):
    wagon = _arrived_wagon(weigher)
    wagon.status = st.GROSS_WEIGHED
    wagon.gross_weight_kg = 91_500
    wagon.save(update_fields=["status", "gross_weight_kg"])

    with patch.object(scale, "read_truck_scale") as read_scale:
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/gross/", {}, format="json"
        )

    assert response.status_code == 400
    assert response.data["code"] == "invalid_wagon_transition"
    read_scale.assert_not_called()


def test_wrong_flow_action_is_rejected_before_reading_scale(
    auth_client,
    weigher,
):
    wagon = _arrived_wagon(weigher, workflow="simple")

    with patch.object(scale, "read_truck_scale") as read_scale:
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/gross/", {}, format="json"
        )

    assert response.status_code == 400
    assert response.data["code"] == "wrong_scale_action"
    read_scale.assert_not_called()


def test_state_change_during_scale_read_rejects_sample_without_weighing(
    auth_client,
    weigher,
):
    wagon = _arrived_wagon(weigher)

    def change_state_while_reading():
        Wagon.objects.filter(pk=wagon.pk).update(status=st.BLOCKED)
        return _reading("90000")

    with patch.object(
        scale,
        "read_truck_scale",
        side_effect=change_state_while_reading,
    ) as read_scale:
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/gross/", {}, format="json"
        )

    assert response.status_code == 400
    assert response.data["code"] == "wagon_changed_during_scale_read"
    assert read_scale.call_count == 1
    wagon.refresh_from_db()
    assert wagon.status == st.BLOCKED
    assert wagon.gross_weight_kg is None
    assert not wagon.weighings.exists()


def test_tare_uses_scale_and_rolls_back_record_when_direction_is_invalid(
    auth_client,
    weigher,
):
    wagon = _arrived_wagon(weigher)
    wagon.status = st.UNLOADING_COMPLETED
    wagon.gross_weight_kg = 20_000
    wagon.save(update_fields=["status", "gross_weight_kg"])

    with patch.object(
        scale,
        "read_truck_scale",
        return_value=_reading("21000"),
    ):
        response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/tare/", {}, format="json"
        )

    assert response.status_code == 400
    assert response.data["code"] == "bad_tare"
    wagon.refresh_from_db()
    assert wagon.status == st.UNLOADING_COMPLETED
    assert wagon.tare_weight_kg is None
    assert not wagon.weighings.exists()


def test_entry_and_exit_actions_use_one_scale_read_each(auth_client, weigher):
    wagon = _arrived_wagon(
        weigher,
        workflow="simple",
        expected_weight_kg=18_000,
    )
    wagon.direction = Wagon.PASSAGE
    wagon.supply = None
    wagon.cargo_name = "Отруби"
    wagon.save(update_fields=["direction", "supply", "cargo_name"])

    with patch.object(
        scale,
        "read_truck_scale",
        side_effect=[_reading("12000"), _reading("30000")],
    ) as read_scale:
        entry = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/entry-weight/", {}, format="json"
        )
        exit_response = auth_client(weigher).post(
            f"/api/grain/wagons/{wagon.pk}/exit-weight/", {}, format="json"
        )

    assert entry.status_code == 200, entry.data
    assert exit_response.status_code == 200, exit_response.data
    assert exit_response.data["net_weight_kg"] == 18_000
    assert exit_response.data["status"] == st.COMPLETED
    assert read_scale.call_count == 2
    assert set(
        WeighingRecord.objects.filter(wagon=wagon).values_list(
            "source", flat=True
        )
    ) == {"scale"}
