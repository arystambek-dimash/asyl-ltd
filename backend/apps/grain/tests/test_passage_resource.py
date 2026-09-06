"""Canonical outbound resource must not expose intake records or commands."""

from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.exceptions import ValidationError

from apps.grain import scale, services, statuses as st
from apps.grain.models import GrainMovement, Wagon

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(user_with_perms):
    return user_with_perms("outbound-resource", codes=[
        "grain.view", "grain.arrive", "grain.weigh", "grain.delete",
    ])


def test_outbound_resource_full_cycle_and_legacy_identity(auth_client, operator):
    client = auth_client(operator)
    response = client.post("/api/grain/passages/", {
        "number": "123 ABC 02", "cargo_name": "Отруби",
    }, format="json")
    assert response.status_code == 201, response.data
    pk = response.data["id"]
    assert response.data["direction"] == "passage"
    for action, weight in [("entry", 12000), ("exit", 30000)]:
        reading = scale.ScaleReading(
            weight_kg=Decimal(weight), age_seconds=Decimal("0.2"),
            updated_at="2026-09-06T10:00:00Z",
        )
        with patch.object(scale, "read_truck_scale", return_value=reading):
            response = client.post(
                f"/api/grain/passages/{pk}/{action}-weight/", {}, format="json",
            )
        assert response.status_code == 200, response.data
    assert response.data["status"] == st.COMPLETED
    assert response.data["net_weight_kg"] == 18000
    assert response.data["entry_weight_kg"] == 12000
    assert response.data["exit_weight_kg"] == 30000
    assert not GrainMovement.objects.filter(wagon_id=pk).exists()
    assert client.get(f"/api/grain/wagons/{pk}/").data["id"] == pk
    timeline = client.get(f"/api/grain/passages/{pk}/timeline/")
    assert timeline.status_code == 200
    assert "Вывоз" in timeline.data[-1]["message"]
    assert all("Вагон" not in event["message"] for event in timeline.data)
    assert any("вес пустой на въезде" in event["message"] for event in timeline.data)
    assert any("вес гружёной на выезде" in event["message"] for event in timeline.data)


def test_outbound_scope_cannot_be_overridden_with_intake_id(auth_client, operator):
    intake = Wagon.objects.create(direction=Wagon.INTAKE, status=st.ARRIVED)
    passage = Wagon.objects.create(direction=Wagon.PASSAGE, status=st.ARRIVED)
    client = auth_client(operator)
    assert [r["id"] for r in client.get("/api/grain/passages/").data] == [passage.pk]
    assert client.get("/api/grain/passages/?direction=intake").data == []
    for suffix in ["", "timeline/"]:
        assert client.get(f"/api/grain/passages/{intake.pk}/{suffix}").status_code == 404
    for action in ["entry-weight", "exit-weight"]:
        with patch.object(scale, "read_truck_scale") as read:
            assert client.post(f"/api/grain/passages/{intake.pk}/{action}/").status_code == 404
            read.assert_not_called()
    assert client.delete(f"/api/grain/passages/{intake.pk}/delete/",
                         {"reason": "Ошибочная запись"}, format="json").status_code == 404
    assert Wagon.objects.filter(pk=intake.pk).exists()


@pytest.mark.parametrize("command", ["lab", "assign-silo", "gross", "tare", "inventory", "approve"])
def test_outbound_api_has_no_intake_commands(auth_client, operator, command):
    passage = Wagon.objects.create(direction=Wagon.PASSAGE, status=st.ARRIVED)
    assert auth_client(operator).post(f"/api/grain/passages/{passage.pk}/{command}/").status_code == 404


def test_outbound_transition_rejects_intake_path_even_through_service():
    passage = Wagon(direction=Wagon.PASSAGE, status=st.ARRIVED)
    with pytest.raises(ValidationError):
        services.ensure_transition(passage, st.GROSS_WEIGHED)
    services.ensure_transition(passage, st.AT_SILO)
    services.ensure_transition(Wagon(direction=Wagon.INTAKE, status=st.ARRIVED), st.GROSS_WEIGHED)


def test_outbound_requires_command_permissions(auth_client, user_with_perms):
    reader = user_with_perms("outbound-reader", codes=["grain.view"])
    client = auth_client(reader)
    assert client.get("/api/grain/passages/").status_code == 200
    assert client.post("/api/grain/passages/", {"cargo_name": "Отруби"}, format="json").status_code == 403


def test_outbound_rejects_invalid_create_payload_without_writes(auth_client, operator):
    client = auth_client(operator)
    for cargo in ["", "x" * 101, {"name": "Отруби"}]:
        assert client.post("/api/grain/passages/", {"cargo_name": cargo}, format="json").status_code == 400
    assert not Wagon.objects.exists()


def test_trusted_frontend_can_send_weight_idempotency_header(api_client, settings):
    settings.CORS_ALLOWED_ORIGINS = ["http://localhost:3000"]
    response = api_client.options(
        "/api/grain/passages/1/entry-weight/",
        HTTP_ORIGIN="http://localhost:3000",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type,idempotency-key",
    )
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "idempotency-key" in response["Access-Control-Allow-Headers"]
    untrusted = api_client.options(
        "/api/grain/passages/1/entry-weight/",
        HTTP_ORIGIN="http://untrusted.invalid",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert "Access-Control-Allow-Origin" not in untrusted
