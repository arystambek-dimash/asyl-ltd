"""Read-only status and privately signed evidence for shipping recognition."""

from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core import signing
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image

from apps.cameras import ai, shipping_automation
from apps.cameras.api_views.shipping_automation import IMAGE_SALT
from apps.cameras.models import (
    AiCountingSession,
    MonoblockCameraSettings,
    ShippingTransportCamera,
    ShippingTransportRecognitionEvent,
    ShippingTransportState,
)
from apps.cameras.tests.test_shipping_tracking import body_tracking
from apps.clients.models import Client
from apps.orders.models import Order
from apps.sales.models import Department

pytestmark = pytest.mark.django_db
STATUS = "/api/cameras/shipping-transport/"
HISTORY = STATUS + "history/"


@pytest.fixture
def review(settings, tmp_path, user_with_perms, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setattr(
        ai,
        "_request",
        Mock(side_effect=AssertionError("read APIs cannot call live CV")),
    )
    monkeypatch.setattr(
        shipping_automation,
        "poll_once",
        Mock(side_effect=AssertionError("GET cannot run automation")),
    )
    MonoblockCameraSettings.objects.create(camera_sources=["cam2", "cam3"])
    own = Department.objects.create(code="review-own", name="Own")
    foreign = Department.objects.create(code="review-foreign", name="Foreign")
    user = user_with_perms("recognition-review", codes=["shipping.view"])
    user.employee.sales_department = own
    user.employee.save(update_fields=["sales_department"])
    own_client = Client.objects.create_with_user(
        first_name="Own", phone="review-own", department=own
    )
    other_client = Client.objects.create_with_user(
        first_name="Other", phone="review-other", department=foreign
    )
    own_order = Order.objects.create(client=own_client, status="loading")
    other_order = Order.objects.create(client=other_client, status="loading")
    image = BytesIO()
    Image.new("RGB", (5, 4), (100, 80, 20)).save(image, format="JPEG")
    return SimpleNamespace(
        user=user,
        own=own,
        foreign=foreign,
        own_order=own_order,
        other_order=other_order,
        jpeg=image.getvalue(),
    )


def _event(
    review, *, order=None, camera="cam2", image=True, offset=0, status="no_order"
):
    moment = timezone.now() + timedelta(seconds=offset)
    event = ShippingTransportRecognitionEvent.objects.create(
        conveyor_camera=camera,
        number_camera="cam7",
        recognition_model="vehicle_number",
        number="123ABC02",
        first_seen_at=moment,
        last_seen_at=moment,
        status=status,
        order=order,
    )
    if image:
        event.image.save(
            f"evidence-{event.pk}.jpg", ContentFile(review.jpeg), save=True
        )
    return event


def _url(event, token=""):
    return f"{HISTORY}{event.pk}/image/?token={token}"


def _token(event, user):
    return signing.dumps({"id": event.pk, "user_id": user.pk}, salt=IMAGE_SALT)


def _state(binding, *, session=None):
    return ShippingTransportState.objects.create(
        binding=binding,
        conveyor_camera=binding.conveyor_camera,
        configuration_updated_at=binding.updated_at,
        state="loading" if session else "confirming",
        detail="Current recognition",
        number="123ABC02",
        candidate_number="123ABC02",
        observed_at=timezone.now(),
        polled_at=timezone.now(),
        confirmations=2,
        frame_ids=["frame-one", "frame-two"],
        session=session,
    )


def test_manual_takeover_cannot_expose_previous_orders_number_or_finish_state(review, auth_client):
    binding = ShippingTransportCamera.objects.create(
        conveyor_camera="cam2", number_camera="cam7", recognition_model="vehicle_number"
    )
    old = AiCountingSession.objects.create(
        order=review.other_order, camera="cam2", status=AiCountingSession.CLOSED,
    )
    state = _state(binding, session=old)
    state.auto_finish = {"state": "completed", "detail": "private previous order", "observed_at": timezone.now().isoformat(), "remaining_seconds": 0}
    state.tracking = body_tracking(timezone.now())
    state.save()
    current = AiCountingSession.objects.create(
        order=review.own_order, camera="cam2", status=AiCountingSession.ACTIVE,
    )
    rows = auth_client(review.user).get(STATUS).data
    row = next(item for item in rows if item["conveyor_camera"] == "cam2")
    assert row["session_id"] == current.pk
    assert row["order_id"] == review.own_order.pk
    assert row["number"] is None
    assert row["auto_finish"] is None
    assert row["tracking"]["presence"] == "unknown"
    assert row["state"] == "busy"


def test_completed_order_keeps_finish_snapshot_after_conveyor_changes_owner(review, auth_client):
    completed = {"state": "completed", "detail": "Погрузка завершена автоматически", "observed_at": (timezone.now() - timedelta(days=2)).isoformat(), "remaining_seconds": 0}
    session = AiCountingSession.objects.create(
        order=review.own_order, camera="cam2", status=AiCountingSession.CLOSED,
        last_status={"auto_finish": completed},
    )
    event = _event(review, order=review.own_order, status="matched")
    event.session = session
    event.save()
    response = auth_client(review.user).get(HISTORY, {"order_id": review.own_order.pk})
    assert response.status_code == 200
    assert response.data[0]["auto_finish"] == completed


def test_status_get_is_read_only_and_does_not_start_background_work(
    review, auth_client
):
    before = (
        ShippingTransportCamera.objects.count(),
        ShippingTransportState.objects.count(),
        ShippingTransportRecognitionEvent.objects.count(),
        AiCountingSession.objects.count(),
    )
    with CaptureQueriesContext(connection) as queries:
        response = auth_client(review.user).get(STATUS)
    assert response.status_code == 200
    assert {row["conveyor_camera"] for row in response.data} == {"cam2", "cam3"}
    assert all(row["order_id"] is None for row in response.data)
    assert all(row["number"] is None for row in response.data)
    assert before == (
        ShippingTransportCamera.objects.count(),
        ShippingTransportState.objects.count(),
        ShippingTransportRecognitionEvent.objects.count(),
        AiCountingSession.objects.count(),
    )
    assert not any(
        query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for query in queries
    )
    ai._request.assert_not_called()
    shipping_automation.poll_once.assert_not_called()


def test_status_hides_foreign_order_number_and_session_but_exposes_busy_camera(
    review, auth_client
):
    binding = ShippingTransportCamera.objects.create(
        conveyor_camera="cam2",
        number_camera="cam7",
        recognition_model="vehicle_number",
    )
    session = AiCountingSession.objects.create(
        order=review.other_order, camera="cam2", status=AiCountingSession.ACTIVE
    )
    state = _state(binding, session=session)
    state.tracking = body_tracking(timezone.now())
    state.tracking_alert = "Unexpected transport for a private order"
    state.save()
    response = auth_client(review.user).get(STATUS)
    row = next(row for row in response.data if row["conveyor_camera"] == "cam2")
    assert row["state"] == "busy"
    assert row["tracking"]["presence"] == "unknown"
    assert row["tracking"]["visit_id"] is None
    assert row["tracking_alert"] == ""
    assert (
        row["number"]
        is row["order_id"]
        is row["session_id"]
        is row["observed_at"]
        is None
    )


def test_status_shows_own_current_session_and_marks_old_observations_stale(
    review, auth_client
):
    binding = ShippingTransportCamera.objects.create(
        conveyor_camera="cam2",
        number_camera="cam7",
        recognition_model="vehicle_number",
    )
    session = AiCountingSession.objects.create(
        order=review.own_order, camera="cam2", status=AiCountingSession.ACTIVE
    )
    state = _state(binding, session=session)
    client = auth_client(review.user)
    response = client.get(STATUS)
    row = next(row for row in response.data if row["conveyor_camera"] == "cam2")
    assert (row["number"], row["order_id"], row["session_id"]) == (
        state.number,
        review.own_order.pk,
        session.pk,
    )
    ShippingTransportState.objects.filter(pk=state.pk).update(
        polled_at=timezone.now() - timedelta(minutes=2)
    )
    row = next(
        row for row in client.get(STATUS).data if row["conveyor_camera"] == "cam2"
    )
    assert row["state"] == "error"
    session.refresh_from_db()
    assert session.status == AiCountingSession.ACTIVE


def test_changed_binding_does_not_display_previous_configuration_number(
    review, auth_client
):
    binding = ShippingTransportCamera.objects.create(
        conveyor_camera="cam2",
        number_camera="cam7",
        recognition_model="vehicle_number",
    )
    _state(binding)
    binding.number_camera = "cam8"
    binding.save()
    row = next(
        row
        for row in auth_client(review.user).get(STATUS).data
        if row["conveyor_camera"] == "cam2"
    )
    assert row["number_camera"] == "cam8"
    assert row["number"] is None
    assert row["state"] == "waiting_number"


def test_counting_heartbeat_does_not_make_stale_transport_presence_fresh(
    review, auth_client
):
    binding = ShippingTransportCamera.objects.create(
        conveyor_camera="cam2", number_camera="cam7", recognition_model="vehicle_number"
    )
    session = AiCountingSession.objects.create(
        order=review.own_order, camera="cam2", status=AiCountingSession.ACTIVE
    )
    state = _state(binding, session=session)
    state.tracking = body_tracking(timezone.now() - timedelta(seconds=20))
    state.tracking_alert = "Транспорт сменился; проверьте погрузку"
    state.save()
    row = next(row for row in auth_client(review.user).get(STATUS).data if row["conveyor_camera"] == "cam2")
    assert row["state"] == "loading"
    assert row["tracking"]["presence"] == "unknown"
    assert row["tracking"]["reason"] == "stale_frame"
    assert row["tracking_alert"] == state.tracking_alert


def test_order_details_keep_historical_tracking_and_current_session_alert(
    review, auth_client
):
    binding = ShippingTransportCamera.objects.create(
        conveyor_camera="cam2", number_camera="cam7", recognition_model="vehicle_number"
    )
    session = AiCountingSession.objects.create(
        order=review.own_order, camera="cam2", status=AiCountingSession.ACTIVE
    )
    state = _state(binding, session=session)
    state.tracking_alert = "Транспорт сменился; проверьте погрузку"
    state.save()
    event = _event(review, order=review.own_order, status="matched")
    event.session = session
    event.visit_id = "visit-original"
    event.tracking = body_tracking(timezone.now() - timedelta(hours=2), visit_id="visit-original")
    event.save()
    rows = auth_client(review.user).get(HISTORY, {"order_id": review.own_order.pk}).data
    assert len(rows) == 1
    assert rows[0]["visit_id"] == "visit-original"
    assert rows[0]["tracking"]["presence"] == "present"
    assert rows[0]["tracking_alert"] == state.tracking_alert


def test_unassociated_number_remains_reviewable_without_an_order(
    review, auth_client
):
    event = _event(review, status="observed")
    rows = auth_client(review.user).get(HISTORY, {"conveyor_camera": "cam2"}).data
    assert [row["id"] for row in rows] == [event.pk]
    assert rows[0]["order_id"] is None
    assert rows[0]["status"] == "observed"
    assert rows[0]["tracking"]["presence"] == "unknown"


def test_history_scope_filters_order_and_conveyor_and_keeps_unmatched_review_entries(
    review, auth_client
):
    own = _event(review, order=review.own_order, status="matched")
    other_conveyor = _event(review, order=review.own_order, camera="cam3", offset=1)
    foreign = _event(review, order=review.other_order, offset=2)
    unmatched = _event(review, offset=3)
    client = auth_client(review.user)
    result = client.get(HISTORY)
    assert result.status_code == 200
    assert [row["id"] for row in result.data] == [
        unmatched.pk,
        other_conveyor.pk,
        own.pk,
    ]
    assert foreign.pk not in [row["id"] for row in result.data]
    assert [
        row["id"] for row in client.get(HISTORY, {"order_id": review.own_order.pk}).data
    ] == [other_conveyor.pk, own.pk]
    assert [
        row["id"]
        for row in client.get(
            HISTORY, {"order_id": review.own_order.pk, "conveyor_camera": "cam2"}
        ).data
    ] == [own.pk]
    assert client.get(HISTORY, {"order_id": review.other_order.pk}).data == []


@pytest.mark.parametrize(
    "query",
    [
        {"order_id": "wrong"},
        {"order_id": "0"},
        {"order_id": "-1"},
        {"conveyor_camera": "../cam2"},
        {"conveyor_camera": "cam2main"},
    ],
)
def test_history_rejects_invalid_filter_values(review, auth_client, query):
    assert auth_client(review.user).get(HISTORY, query).status_code == 400


def test_signed_image_from_history_works_without_bearer_and_never_exposes_storage_path(
    review, auth_client, api_client
):
    event = _event(review, order=review.own_order)
    row = auth_client(review.user).get(HISTORY).data[0]
    assert row["id"] == event.pk
    assert "image" not in row
    assert "shipping-transport/20" not in row["image_url"]
    url = urlsplit(row["image_url"])
    token = parse_qs(url.query)["token"][0]
    assert signing.loads(token, salt=IMAGE_SALT)["user_id"] == review.user.pk
    response = api_client.get(url.path + "?" + url.query)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert b"".join(response.streaming_content) == review.jpeg
    response.close()


@pytest.mark.parametrize(
    "kind", ["missing", "tampered", "expired", "wrong_event", "wrong_salt"]
)
def test_image_signature_is_required_and_bound_to_event_and_time(
    review, api_client, kind
):
    event = _event(review, order=review.own_order)
    token = _token(event, review.user)
    if kind == "missing":
        token = ""
    elif kind == "tampered":
        token += "tamper"
    elif kind == "expired":
        with patch(
            "django.core.signing.time.time",
            return_value=timezone.now().timestamp() - 601,
        ):
            token = _token(event, review.user)
    elif kind == "wrong_event":
        token = signing.dumps(
            {"id": event.pk + 1, "user_id": review.user.pk}, salt=IMAGE_SALT
        )
    else:
        token = signing.dumps(
            {"id": event.pk, "user_id": review.user.pk}, salt="different-salt"
        )
    assert api_client.get(_url(event, token)).status_code == 404


@pytest.mark.parametrize("revoke", ["inactive", "permission", "department", "client"])
def test_image_rechecks_current_user_activity_permission_and_department(
    review, api_client, revoke
):
    event = _event(review, order=review.own_order)
    token = _token(event, review.user)
    if revoke == "inactive":
        review.user.is_active = False
        review.user.save(update_fields=["is_active"])
    elif revoke == "permission":
        review.user.employee.permissions.clear()
    elif revoke == "department":
        review.user.employee.sales_department = review.foreign
        review.user.employee.save(update_fields=["sales_department"])
    else:
        review.user.is_client = True
        review.user.save(update_fields=["is_client"])
    assert api_client.get(_url(event, token)).status_code == 404


def test_foreign_order_evidence_cannot_be_read_even_with_valid_signature(
    review, api_client
):
    event = _event(review, order=review.other_order)
    assert api_client.get(_url(event, _token(event, review.user))).status_code == 404


def test_missing_evidence_file_is_a_404_and_history_has_no_unsigned_media_url(
    review, auth_client, api_client
):
    event = _event(review, order=review.own_order, image=False)
    assert auth_client(review.user).get(HISTORY).data[0]["image_url"] is None
    assert api_client.get(_url(event, _token(event, review.user))).status_code == 404
    event.image.save("vanishing.jpg", ContentFile(review.jpeg), save=True)
    event.image.storage.delete(event.image.name)
    assert api_client.get(_url(event, _token(event, review.user))).status_code == 404


@pytest.mark.parametrize("path", [STATUS, HISTORY])
def test_status_and_history_require_staff_permissions(
    review, path, api_client, auth_client, client_user, make_user
):
    assert api_client.get(path).status_code == 401
    assert auth_client(client_user).get(path).status_code == 403
    assert auth_client(make_user("no-review-permissions")).get(path).status_code == 403
    assert auth_client(review.user).post(path, {}, format="json").status_code == 405
