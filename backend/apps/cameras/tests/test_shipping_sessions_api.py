from datetime import datetime, timedelta
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.db import connection
from django.db.models import Max
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image

from apps.cameras import shipping_segments
from apps.cameras.models import (
    AlwaysOnCounterCursor, AlwaysOnImportedEvent, ShippingLoadingEvent, ShippingLoadingSegment,
    ShippingLoadingSession, ShippingSessionSettings,
)
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db
BASE = "/api/cameras/shipping-sessions/"
SETTINGS = "/api/cameras/shipping-session-settings/"


def local(*parts):
    """Wall-clock time at the plant, where business days start at midnight."""
    return timezone.make_aware(datetime(*parts), timezone.get_default_timezone())


def segment(*, kind="vehicle_number", camera="cam2", status="unidentified", order=None, started_at=None):
    started_at = started_at or timezone.now()-timedelta(minutes=8)
    group = ShippingLoadingSession.objects.create(
        camera=camera, recognition_model=kind, status="closed", total_bags=3,
        started_at=started_at, last_counted_at=started_at+timedelta(minutes=2),
        ended_at=started_at+timedelta(minutes=2), order=order,
    )
    previous = AlwaysOnImportedEvent.objects.filter(camera=camera).aggregate(value=Max("upstream_event_id"))["value"] or 0
    events = AlwaysOnImportedEvent.objects.bulk_create([
        AlwaysOnImportedEvent(camera=camera, upstream_event_id=previous+i+1,
                              occurred_at=group.started_at+timedelta(seconds=i), source="sub",
                              mode="always_on", analytics_scope="shipping", applied_to_analytics=True)
        for i in range(3)
    ])
    AlwaysOnCounterCursor.objects.update_or_create(camera=camera, defaults={"last_event_id": previous+3, "last_total": previous+3, "event_compat_total": previous+3})
    row = ShippingLoadingSegment.objects.create(
        session=group, camera=camera, number_camera="cam7", recognition_model=kind,
        identity_status=status, total_bags=3, started_at=group.started_at,
        last_counted_at=group.last_counted_at, ended_at=group.ended_at,
        idle_timeout_seconds=300, first_event=events[0], last_event=events[-1],
        first_upstream_event_id=previous+1, last_upstream_event_id=previous+3,
    )
    # As in the projection, every counted crossing belongs to exactly one segment.
    ShippingLoadingEvent.objects.bulk_create([ShippingLoadingEvent(event=event, segment=row) for event in events])
    return row


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


@pytest.mark.parametrize("by_day", [False, True])
def test_session_list_keeps_segments_and_has_bounded_queries(auth_client, operator, by_day):
    for minute in range(10):
        segment(started_at=local(2026, 9, 10, 12, minute))
    params = {"day": "2026-09-10"} if by_day else {}
    with CaptureQueriesContext(connection) as queries:
        response = auth_client(operator).get(BASE, params)
    assert response.status_code == 200
    assert len(response.data["results"]) == 10
    assert all(x["total_bags"] == 3 and len(x["segments"]) == 1 for x in response.data["results"])
    assert all(sum(color["total"] for color in x["colors"]) == 3 for x in response.data["results"])
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


def test_day_filter_uses_the_plant_calendar_day_newest_first(auth_client, operator):
    segment(started_at=local(2026, 9, 9, 23, 50))
    early = segment(started_at=local(2026, 9, 10, 0, 10))
    late = segment(started_at=local(2026, 9, 10, 21, 4))
    segment(started_at=local(2026, 9, 11, 0, 0))
    segment(camera="cam3", started_at=local(2026, 9, 10, 12, 0))
    response = auth_client(operator).get(BASE, {"camera": "cam2", "day": "2026-09-10"})
    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [late.session_id, early.session_id]
    assert response.data["truncated"] is False


@pytest.mark.parametrize("day", ["2026-13-01", "yesterday"])
def test_day_filter_rejects_malformed_dates(auth_client, operator, day):
    assert auth_client(operator).get(BASE, {"day": day}).status_code == 400


def test_day_listing_is_capped_and_says_so(auth_client, operator, monkeypatch):
    monkeypatch.setattr("apps.cameras.api_views.shipping_sessions.DAY_LIMIT", 2)
    rows = [segment(started_at=local(2026, 9, 10, hour)) for hour in (8, 9, 10)]
    data = auth_client(operator).get(BASE, {"day": "2026-09-10"}).data
    assert [row["id"] for row in data["results"]] == [rows[2].session_id, rows[1].session_id]
    assert data["truncated"] is True


def test_session_colors_count_every_projected_bag_once(auth_client, operator):
    start = timezone.now()-timedelta(hours=2)
    ShippingSessionSettings.objects.update_or_create(singleton=True, defaults={"activated_at": start})
    bags = [("White", ""), (None, "white_50kg"), ("blue", ""), (None, "Red_50"), (None, "")]
    AlwaysOnImportedEvent.objects.bulk_create([
        AlwaysOnImportedEvent(camera="cam2", upstream_event_id=i+1, occurred_at=start+timedelta(seconds=i),
                              source="sub", mode="always_on", analytics_scope="shipping",
                              applied_to_analytics=True, color=color, class_name=class_name)
        for i, (color, class_name) in enumerate(bags)
    ])
    AlwaysOnCounterCursor.objects.update_or_create(camera="cam2", defaults={"last_event_id": 5, "last_total": 5, "event_compat_total": 5})
    shipping_segments.ingest_camera("cam2")
    [row] = auth_client(operator).get(BASE).data["results"]
    assert row["total_bags"] == 5
    assert row["colors"] == [
        {"color": "white", "total": 2, "percent": 40.0},
        {"color": "blue", "total": 1, "percent": 20.0},
        {"color": "red", "total": 1, "percent": 20.0},
        {"color": "unclassified", "total": 1, "percent": 20.0},
    ]


def test_a_page_of_segment_frames_from_one_address_is_never_throttled(
    auth_client, operator, api_client, settings, tmp_path, production_throttling,
):
    settings.MEDIA_ROOT = str(tmp_path)
    frame = BytesIO()
    Image.new("RGB", (8, 8), "gray").save(frame, format="JPEG")
    urls = []
    for row in (segment(), segment(), segment()):
        row.photo.save("frame.jpg", ContentFile(frame.getvalue()))
        urls.append(auth_client(operator).get(f"/api/cameras/shipping-segments/{row.pk}/").data["photo_url"])
    address = {"REMOTE_ADDR": "203.0.113.61"}
    codes = []
    with production_throttling():
        for url in urls * 2:
            response = api_client.get(url, **address)
            codes.append(response.status_code)
            if response.streaming:
                b"".join(response.streaming_content)  # closes the file like a WSGI server
        control = [api_client.post("/api/auth/refresh/", {"refresh": "x"}, format="json", **address).status_code
                   for _ in range(3)]
    assert codes == [200] * 6
    assert control[-1] == 429
