from unittest.mock import patch

import pytest
from rest_framework.exceptions import PermissionDenied

from apps.cameras import ai, counting
from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.eventlog.services import log_event
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.sales.models import Department
from apps.shipments.models import Shipment
from apps.shipments.services import dispatch_order

pytestmark = pytest.mark.django_db


def _owned_client(name, department):
    return Client.objects.create_with_user(
        first_name=name,
        phone=name,
        department=department,
    )


def test_assigned_employee_cannot_mutate_foreign_shipment_but_global_employee_can(
    user_with_perms,
    api_as,
):
    department_a = Department.objects.create(code="shipment-a", name="Отдел A")
    department_b = Department.objects.create(code="shipment-b", name="Отдел B")
    permissions = ["loader.confirm", "loader.trucks"]
    assigned = user_with_perms(
        "shipment-assigned-a",
        codes=permissions,
        department=department_a,
    )
    global_operator = user_with_perms(
        "shipment-global",
        codes=permissions,
    )
    foreign_order = Order.objects.create(
        client=_owned_client("Shipment B", department_b),
        status="confirmed",
        truck_number="01B001",
    )
    scoped_api = api_as(assigned)
    for action in ("dispatch", "rollback"):
        response = scoped_api.post(
            f"/api/loader/orders/{foreign_order.pk}/{action}/",
            {},
            format="json",
        )
        assert response.status_code == 404, action

    foreign_order.refresh_from_db()
    assert foreign_order.status == "confirmed"
    assert not Shipment.objects.filter(order=foreign_order).exists()

    response = api_as(global_operator).post(
        f"/api/loader/orders/{foreign_order.pk}/dispatch/",
        {},
        format="json",
    )

    assert response.status_code == 200
    foreign_order.refresh_from_db()
    assert foreign_order.status == "shipped"


def test_stale_scoped_shipment_request_rechecks_department_under_order_lock(
    user_with_perms,
):
    department_a = Department.objects.create(code="stale-a", name="Старый отдел")
    department_b = Department.objects.create(code="stale-b", name="Новый отдел")
    assigned = user_with_perms(
        "stale-shipment-a",
        codes=["loader.confirm"],
        department=department_a,
    )
    client = _owned_client("Stale transfer", department_a)
    stale_order = Order.objects.create(
        client=client,
        status="confirmed",
        truck_number="01A777",
    )
    client.department = department_b
    client.save(update_fields=["department"])

    with pytest.raises(PermissionDenied):
        dispatch_order(stale_order, assigned)

    stale_order.refresh_from_db()
    assert stale_order.status == "confirmed"
    assert not Shipment.objects.filter(order=stale_order).exists()

    with pytest.raises(PermissionDenied):
        order_services.soft_delete_order(stale_order, assigned)

    stale_order.refresh_from_db()
    assert stale_order.deleted_at is None


def test_camera_sessions_respect_client_ownership(user_with_perms, api_as):
    department_a = Department.objects.create(code="camera-a", name="Камеры A")
    department_b = Department.objects.create(code="camera-b", name="Камеры B")
    permissions = ["monoblock.view", "loader.confirm"]
    assigned = user_with_perms(
        "camera-assigned-a",
        codes=permissions,
        department=department_a,
    )
    global_viewer = user_with_perms(
        "camera-global",
        codes=permissions,
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

    sessions_response = api_as(assigned).get("/api/cameras/ai/sessions/")
    global_sessions = api_as(global_viewer).get("/api/cameras/ai/sessions/")

    assert sessions_response.status_code == 200
    assert {row["id"] for row in sessions_response.data} == {own_open.pk}
    assert {row["id"] for row in global_sessions.data} == {
        own_open.pk,
        foreign_open.pk,
    }


def test_camera_mutations_recheck_transferred_client_before_edge_side_effects(
    user_with_perms,
):
    department_a = Department.objects.create(
        code="camera-stale-a",
        name="Камеры старого отдела",
    )
    department_b = Department.objects.create(
        code="camera-stale-b",
        name="Камеры нового отдела",
    )
    assigned = user_with_perms(
        "camera-stale-assigned-a",
        codes=["monoblock.view", "loader.confirm"],
        department=department_a,
    )
    client = _owned_client("Camera transferred", department_a)
    order = Order.objects.create(client=client, status="loading")
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam99",
        status=AiCountingSession.ACTIVE,
        started_by=assigned,
    )
    client.department = department_b
    client.save(update_fields=["department"])

    with (
        patch.object(ai, "status") as edge_status,
        patch.object(ai, "delete") as edge_delete,
    ):
        with pytest.raises(PermissionDenied):
            counting.stop("cam99", order, assigned)
        edge_status.assert_not_called()
        edge_delete.assert_not_called()

    # The idempotent no-session response is still an order-owned read and must
    # not disclose status/shipment data from a transferred client.
    session.delete()
    with pytest.raises(PermissionDenied):
        counting.stop("cam99", order, assigned)


def test_event_log_hides_foreign_order_events_but_unassigned_viewer_is_global(
    user_with_perms,
    api_as,
):
    department_a = Department.objects.create(code="events-a", name="События A")
    department_b = Department.objects.create(code="events-b", name="События B")
    assigned = user_with_perms(
        "events-assigned-a",
        codes=["events.view"],
        department=department_a,
    )
    global_viewer = user_with_perms(
        "events-global",
        codes=["events.view"],
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

    scoped_api = api_as(assigned)
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

    global_response = api_as(global_viewer).get("/api/events/")
    global_ids = {row["id"] for row in global_response.data["results"]}
    assert {
        own_event.pk,
        foreign_event.pk,
        system_event.pk,
        own_client_event.pk,
        foreign_client_event.pk,
    } <= global_ids


def test_event_log_keeps_history_of_orders_in_recycle_bin_and_archive(
    user_with_perms,
    api_as,
):
    department_a = Department.objects.create(code="trash-a", name="Корзина A")
    department_b = Department.objects.create(code="trash-b", name="Корзина B")
    assigned = user_with_perms(
        "trash-events-a",
        codes=["events.view"],
        department=department_a,
    )
    global_viewer = user_with_perms(
        "trash-events-global",
        codes=["events.view"],
    )
    trashed = Order.objects.create(
        client=_owned_client("Trash A", department_a), status="confirmed")
    archived = Order.objects.create(
        client=_owned_client("Archive A", department_a), status="confirmed")
    foreign_trashed = Order.objects.create(
        client=_owned_client("Trash B", department_b), status="confirmed")
    status_event = log_event("status", "before trash", order=trashed)

    order_services.soft_delete_order(trashed, global_viewer)
    order_services.soft_delete_order(archived, global_viewer)
    order_services.purge_order(archived, global_viewer)
    order_services.soft_delete_order(foreign_trashed, global_viewer)
    trash_event = EventLog.objects.get(
        order=trashed, message="Заказ удалён в корзину")
    purge_event = EventLog.objects.get(
        order=archived, message__contains="удалён из архива")
    foreign_trash_event = EventLog.objects.get(
        order=foreign_trashed, message="Заказ удалён в корзину")

    # Удаление в корзину и из архива — то, что журнал обязан показать
    # при разборе: заказ из корзины не должен пропадать из истории.
    global_response = api_as(global_viewer).get("/api/events/")
    global_ids = {row["id"] for row in global_response.data["results"]}
    assert {
        status_event.pk,
        trash_event.pk,
        purge_event.pk,
        foreign_trash_event.pk,
    } <= global_ids

    scoped_api = api_as(assigned)
    scoped_ids = {
        row["id"] for row in scoped_api.get("/api/events/").data["results"]
    }
    assert {status_event.pk, trash_event.pk, purge_event.pk} <= scoped_ids
    assert foreign_trash_event.pk not in scoped_ids
    targeted = scoped_api.get("/api/events/", {"order": trashed.pk})
    assert {row["id"] for row in targeted.data["results"]} == {
        status_event.pk,
        trash_event.pk,
    }
