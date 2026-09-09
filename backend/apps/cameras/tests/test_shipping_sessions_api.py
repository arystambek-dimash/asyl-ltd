from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.db import connection
from django.db.models import Max
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image

from apps.cameras.models import AlwaysOnCounterCursor, AlwaysOnImportedEvent, ShippingLoadingSession, ShippingLoadingSegment, ShippingSessionSettings
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db
BASE = "/api/cameras/shipping-sessions/"
SETTINGS = "/api/cameras/shipping-session-settings/"


def segment(*, kind="vehicle_number", camera="cam2", status="unidentified", order=None):
    now = timezone.now()
    group = ShippingLoadingSession.objects.create(
        camera=camera, recognition_model=kind, status="closed", total_bags=3,
        started_at=now-timedelta(minutes=8), last_counted_at=now-timedelta(minutes=6),
        ended_at=now-timedelta(minutes=6), order=order,
    )
    previous = AlwaysOnImportedEvent.objects.filter(camera=camera).aggregate(value=Max("upstream_event_id"))["value"] or 0
    events = AlwaysOnImportedEvent.objects.bulk_create([
        AlwaysOnImportedEvent(camera=camera, upstream_event_id=previous+i+1,
                              occurred_at=group.started_at+timedelta(seconds=i), source="sub",
                              mode="always_on", analytics_scope="shipping", applied_to_analytics=True)
        for i in range(3)
    ])
    AlwaysOnCounterCursor.objects.update_or_create(camera=camera, defaults={"last_event_id": previous+3, "last_total": previous+3, "event_compat_total": previous+3})
    return ShippingLoadingSegment.objects.create(
        session=group, camera=camera, number_camera="cam7", recognition_model=kind,
        identity_status=status, total_bags=3, started_at=group.started_at,
        last_counted_at=group.last_counted_at, ended_at=group.ended_at,
        idle_timeout_seconds=300, first_event=events[0], last_event=events[-1],
        first_upstream_event_id=previous+1, last_upstream_event_id=previous+3,
    )


def test_settings_operator_can_change_timeout_and_change_is_audited(auth_client, operator):
    client = auth_client(operator)
    assert client.get(SETTINGS).data["idle_timeout_seconds"] == 300
    response = client.patch(SETTINGS, {"idle_timeout_seconds": 90}, format="json")
    assert response.status_code == 200
    assert response.data == {"idle_timeout_seconds": 90, "can_manage": True}
    assert ShippingSessionSettings.objects.get(singleton=True).idle_timeout_seconds == 90
    assert EventLog.objects.filter(event_type="shipping_idle_timeout_changed", user=operator).exists()


@pytest.mark.parametrize("value", [0, -1, 29, 86401, True, "300", 300.4])
def test_settings_reject_invalid_timeout_without_writing(auth_client, operator, value):
    before = list(ShippingSessionSettings.objects.values_list("idle_timeout_seconds", flat=True))
    assert auth_client(operator).patch(SETTINGS, {"idle_timeout_seconds": value}, format="json").status_code == 400
    assert list(ShippingSessionSettings.objects.values_list("idle_timeout_seconds", flat=True)) == before


def test_read_only_staff_cannot_change_timeout_or_bind_number(auth_client, user_with_perms):
    staff = user_with_perms("segment-reader", codes=["shipping.view"])
    row = segment()
    client = auth_client(staff)
    response = client.get(BASE)
    assert response.status_code == 200
    assert response.data["results"][0]["segments"][0]["can_identify"] is False
    assert client.patch(SETTINGS, {"idle_timeout_seconds": 120}, format="json").status_code == 403
    assert client.post(f"/api/cameras/shipping-segments/{row.pk}/identify/", {"number": "123ABC02"}, format="json").status_code == 403


def test_session_list_keeps_segments_and_has_bounded_queries(auth_client, operator):
    for _ in range(10):
        segment()
    with CaptureQueriesContext(connection) as queries:
        response = auth_client(operator).get(BASE)
    assert response.status_code == 200
    assert len(response.data["results"]) == 10
    assert all(x["total_bags"] == 3 and len(x["segments"]) == 1 for x in response.data["results"])
    assert len(queries) <= 10
    assert not any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)


def test_reader_cannot_view_other_transport_type(auth_client, operator):
    own = segment()
    foreign = segment(kind="wagon_number", camera="cam3")
    client = auth_client(operator)
    assert [x["id"] for x in client.get(BASE).data["results"]] == [own.session_id]
    assert client.get(f"/api/cameras/shipping-segments/{foreign.pk}/").status_code == 404


def test_private_photo_rechecks_user_access(auth_client, operator, api_client, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    row = segment()
    frame = BytesIO()
    Image.new("RGB", (8, 8), "gray").save(frame, format="JPEG")
    row.photo.save("test-frame.jpg", ContentFile(frame.getvalue()))
    detail = auth_client(operator).get(f"/api/cameras/shipping-segments/{row.pk}/").data
    photo = api_client.get(detail["photo_url"])
    assert photo.status_code == 200
    assert photo["Cache-Control"] == "private, no-store"
    operator.is_active = False
    operator.save(update_fields=["is_active"])
    assert api_client.get(detail["photo_url"]).status_code == 404
    assert api_client.get(f"/api/cameras/shipping-segments/{row.pk}/photo/").status_code == 404
    photo.close()


def test_number_input_cannot_overwrite_already_identified_segment(auth_client, operator):
    row = segment(status="identified")
    response = auth_client(operator).post(f"/api/cameras/shipping-segments/{row.pk}/identify/", {"number": "123ABC02"}, format="json")
    assert response.status_code == 403


def test_manual_number_is_audited_without_changing_counts(auth_client, operator):
    row = segment()
    response = auth_client(operator).post(f"/api/cameras/shipping-segments/{row.pk}/identify/", {"number": "123ABC02"}, format="json")
    assert response.status_code == 200
    row.refresh_from_db()
    assert row.number == "123ABC02" and row.number_source == "manual"
    assert row.total_bags == row.session.total_bags == 3
    assert EventLog.objects.filter(event_type="shipping_loading_identified", user=operator).exists()


def test_unknown_kind_cannot_bypass_wagon_permission(auth_client, operator):
    row = segment(kind="")
    response = auth_client(operator).post(f"/api/cameras/shipping-segments/{row.pk}/identify/", {"number": "12345674"}, format="json")
    assert response.status_code == 403
    row.refresh_from_db()
    assert row.number == ""


def test_merged_empty_redirects_are_not_listed(auth_client, operator):
    row = segment()
    old = segment()
    old.session.status = "merged"
    old.session.save(update_fields=["status"])
    response = auth_client(operator).get(BASE)
    assert [item["id"] for item in response.data["results"]] == [row.session_id]


def test_old_active_group_stays_on_first_page_after_merging(auth_client, operator):
    active = segment()
    ShippingLoadingSession.objects.filter(pk=active.session_id).update(status="active", ended_at=None)
    for _ in range(21):
        segment(camera="cam3")
    client = auth_client(operator)
    response = client.get(BASE).data
    assert response["results"][0]["id"] == active.session_id
    assert len(response["results"]) == 21
    next_page = client.get(BASE, {"cursor": response["next_cursor"]}).data
    assert len(next_page["results"]) == 1
    assert next_page["results"][0]["id"] != active.session_id


def test_department_scope_is_checked_on_details_and_list(auth_client, operator):
    from apps.clients.models import Client
    from apps.orders.models import Order
    from apps.sales.models import Department
    own = Department.objects.create(code="seg-own", name="Own")
    other = Department.objects.create(code="seg-other", name="Other")
    operator.employee.sales_department = own
    operator.employee.save(update_fields=["sales_department"])
    client = Client.objects.create_with_user(first_name="Other", phone="segment-scope", department=other)
    row = segment(order=Order.objects.create(client=client))
    api = auth_client(operator)
    assert api.get(BASE).data["results"] == []
    assert api.get(f"/api/cameras/shipping-segments/{row.pk}/").status_code == 404
