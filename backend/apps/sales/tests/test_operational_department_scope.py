from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cameras import ai, counting, recordings, sessions
from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.eventlog.services import log_event
from apps.orders.models import Order
from apps.sales.models import Department
from apps.shipments import scale
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _employee(user_with_perms, username, codes, department=None):
    user = user_with_perms(username, codes=codes)
    if department is not None:
        user.employee.sales_department = department
        user.employee.save(update_fields=["sales_department"])
    return user


def _owned_client(name, department):
    return Client.objects.create_with_user(
        first_name=name,
        phone=name,
        department=department,
    )


def test_assigned_employee_cannot_mutate_foreign_shipment_but_global_employee_can(
    user_with_perms,
    monkeypatch,
):
    department_a = Department.objects.create(code="shipment-a", name="Отдел A")
    department_b = Department.objects.create(code="shipment-b", name="Отдел B")
    permissions = ["shipping.arrive", "shipping.load", "shipping.ship"]
    assigned = _employee(
        user_with_perms,
        "shipment-assigned-a",
        permissions,
        department_a,
    )
    global_operator = _employee(
        user_with_perms,
        "shipment-global",
        permissions,
    )
    foreign_order = Order.objects.create(
        client=_owned_client("Shipment B", department_b),
        status="confirmed",
        truck_number="01B001",
    )
    monkeypatch.setattr(scale, "enabled", lambda: False)

    scoped_api = _api(assigned)
    for suffix, payload in (
        ("arrive", {"weigh_in_kg": "8000"}),
        ("load", {"bags": 1}),
        ("finish-loading", {}),
        ("rewind-loading", {}),
        ("ship", {}),
    ):
        response = scoped_api.post(
            f"/api/orders/{foreign_order.pk}/{suffix}/",
            payload,
            format="json",
        )
        assert response.status_code == 404, suffix

    foreign_order.refresh_from_db()
    assert foreign_order.status == "confirmed"
    assert not Shipment.objects.filter(order=foreign_order).exists()

    response = _api(global_operator).post(
        f"/api/orders/{foreign_order.pk}/arrive/",
        {"weigh_in_kg": "8000"},
        format="json",
    )

    assert response.status_code == 200
    foreign_order.refresh_from_db()
    assert foreign_order.status == "arrived"


def test_camera_sessions_history_recordings_and_status_respect_client_ownership(
    user_with_perms,
    monkeypatch,
):
    department_a = Department.objects.create(code="camera-a", name="Камеры A")
    department_b = Department.objects.create(code="camera-b", name="Камеры B")
    permissions = ["shipping.view", "shipping.load"]
    assigned = _employee(
        user_with_perms,
        "camera-assigned-a",
        permissions,
        department_a,
    )
    global_viewer = _employee(
        user_with_perms,
        "camera-global",
        permissions,
    )
    own_order = Order.objects.create(
        client=_owned_client("Camera A", department_a),
        status="loading",
    )
    foreign_order = Order.objects.create(
        client=_owned_client("Camera B", department_b),
        status="loading",
    )
    own_open = AiCountingSession.objects.create(
        order=own_order,
        camera="cam1",
        status=AiCountingSession.ACTIVE,
        started_by=assigned,
    )
    foreign_open = AiCountingSession.objects.create(
        order=foreign_order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=global_viewer,
    )
    own_closed = AiCountingSession.objects.create(
        order=own_order,
        camera="cam3",
        status=AiCountingSession.CLOSED,
        ended_at=timezone.now(),
    )
    foreign_recording = AiCountingSession.objects.create(
        order=foreign_order,
        camera="cam4",
        status=AiCountingSession.CLOSED,
        ended_at=timezone.now(),
        recording_stream="cam4ai",
    )
    monkeypatch.setattr(ai, "AI_KEY", "scope-test-key")

    scoped_api = _api(assigned)
    sessions_response = scoped_api.get("/api/cameras/ai/sessions/")
    history_response = scoped_api.get("/api/cameras/ai/history/")
    foreign_history = scoped_api.get(
        "/api/cameras/ai/history/",
        {"order_id": foreign_order.pk},
    )

    assert sessions_response.status_code == 200
    assert {row["id"] for row in sessions_response.data} == {own_open.pk}
    assert history_response.status_code == 200
    assert {row["id"] for row in history_response.data} == {
        own_open.pk,
        own_closed.pk,
    }
    assert foreign_history.status_code == 200
    assert foreign_history.data == []

    with patch.object(recordings, "list_segments", return_value=[]) as listing:
        foreign_metadata = scoped_api.get(
            f"/api/cameras/ai/history/{foreign_recording.pk}/recording/"
        )
        assert foreign_metadata.status_code == 404
        listing.assert_not_called()

        global_metadata = _api(global_viewer).get(
            f"/api/cameras/ai/history/{foreign_recording.pk}/recording/"
        )
        assert global_metadata.status_code == 200
        listing.assert_called_once()

    with patch.object(counting, "get_status", return_value={"running": False}) as status:
        foreign_status = scoped_api.get(
            f"/api/cameras/cam2/ai/?order_id={foreign_order.pk}"
        )
        assert foreign_status.status_code == 404
        status.assert_not_called()

        global_status = _api(global_viewer).get(
            f"/api/cameras/cam2/ai/?order_id={foreign_order.pk}"
        )
        assert global_status.status_code == 200
        status.assert_called_once_with(
            "cam2",
            foreign_order.pk,
            global_viewer,
        )

    # A status poll without order_id must not reveal another department's
    # session identifiers through counting.metadata().
    unbound_status = scoped_api.get("/api/cameras/cam2/ai/")
    assert unbound_status.status_code in (200, 404)
    assert "session_id" not in unbound_status.data
    assert "session_order_id" not in unbound_status.data

    with patch.object(
        counting,
        "start",
        side_effect=sessions.AiSessionBusy(foreign_open),
    ):
        foreign_busy = scoped_api.post(
            "/api/cameras/cam2/ai/",
            {"order_id": own_order.pk},
            format="json",
        )
    assert foreign_busy.status_code == 409
    assert "session_id" not in foreign_busy.data
    assert "session_order_id" not in foreign_busy.data
    assert str(foreign_order.pk) not in foreign_busy.data["detail"]

    global_sessions = _api(global_viewer).get("/api/cameras/ai/sessions/")
    global_history = _api(global_viewer).get("/api/cameras/ai/history/")
    assert {row["id"] for row in global_sessions.data} == {
        own_open.pk,
        foreign_open.pk,
    }
    assert {row["id"] for row in global_history.data} == {
        own_open.pk,
        foreign_open.pk,
        own_closed.pk,
        foreign_recording.pk,
    }


def test_event_log_hides_foreign_order_events_but_unassigned_viewer_is_global(
    user_with_perms,
):
    department_a = Department.objects.create(code="events-a", name="События A")
    department_b = Department.objects.create(code="events-b", name="События B")
    assigned = _employee(
        user_with_perms,
        "events-assigned-a",
        ["events.view"],
        department_a,
    )
    global_viewer = _employee(
        user_with_perms,
        "events-global",
        ["events.view"],
    )
    own_order = Order.objects.create(client=_owned_client("Events A", department_a))
    foreign_order = Order.objects.create(client=_owned_client("Events B", department_b))
    own_event = log_event(
        event_type="status",
        message="own",
        order=own_order,
    )
    foreign_event = log_event(
        event_type="status",
        message="foreign",
        order=foreign_order,
    )
    system_event = EventLog.objects.create(
        event_type="system",
        message="global",
    )
    own_client_event = EventLog.objects.create(
        event_type="client_security",
        message="own client event",
        payload={"client_id": own_order.client_id},
    )
    foreign_client_event = EventLog.objects.create(
        event_type="client_security",
        message="foreign client event",
        payload={"client_id": foreign_order.client_id},
    )

    scoped_api = _api(assigned)
    response = scoped_api.get("/api/events/")
    targeted = scoped_api.get("/api/events/", {"order": foreign_order.pk})

    assert response.status_code == 200
    visible_ids = {row["id"] for row in response.data["results"]}
    assert own_event.pk in visible_ids
    assert own_client_event.pk in visible_ids
    assert system_event.pk not in visible_ids
    assert foreign_event.pk not in visible_ids
    assert foreign_client_event.pk not in visible_ids
    assert targeted.status_code == 200
    assert targeted.data["count"] == 0

    # Hard deletion nulls EventLog.order. The immutable client snapshot keeps
    # the own event visible without turning the foreign event into a global one.
    Order.all_objects.filter(pk__in=[own_order.pk, foreign_order.pk]).delete()
    after_hard_delete = scoped_api.get("/api/events/")
    after_delete_ids = {
        row["id"] for row in after_hard_delete.data["results"]
    }
    assert own_event.pk in after_delete_ids
    assert foreign_event.pk not in after_delete_ids

    global_response = _api(global_viewer).get("/api/events/")
    global_ids = {row["id"] for row in global_response.data["results"]}
    assert {
        own_event.pk,
        foreign_event.pk,
        system_event.pk,
        own_client_event.pk,
        foreign_client_event.pk,
    } <= global_ids
