"""API разметки датасета ориентации: список, фильтры, метки, сводка."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from apps.cameras import ai as camera_ai
from apps.grain import orientation_dataset as dataset
from apps.grain import statuses as st
from apps.grain import views as grain_views
from apps.grain.models import (
    UnassignedWeighing,
    VehicleOrientationSample,
    Wagon,
    WeighingRecord,
)
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.core.cache import cache
from django.utils import timezone

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 32
LIST_URL = "/api/grain/orientation-samples/"


@pytest.fixture(autouse=True)
def _fresh_camera_pc_cache():
    cache.delete(grain_views.ORIENTATION_PC_CACHE_KEY)
    yield
    cache.delete(grain_views.ORIENTATION_PC_CACHE_KEY)


@pytest.fixture(autouse=True)
def dataset_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.VEHICLE_ORIENTATION_DATASET_ENABLED = True
    settings.VEHICLE_ORIENTATION_EMPTY_MAX_KG = 5000
    settings.VEHICLE_ORIENTATION_LOADED_MIN_KG = 6000
    settings.VEHICLE_ORIENTATION_SAMPLE_MAX_AGE_DAYS = 60


@pytest.fixture
def viewer(user_with_perms):
    return user_with_perms("orientation-viewer", codes=["grain.view"])


@pytest.fixture
def admin(user_with_perms):
    return user_with_perms("orientation-admin", codes=["grain.view", "grain.admin"])


def _trip(number="854ANB13", *, status=st.COMPLETED, gross=3880, tare=8760):
    return Wagon.objects.create(
        number=number,
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=status,
        arrived_at=timezone.now() - timedelta(hours=1),
        gross_weight_kg=gross,
        tare_weight_kg=tare,
        number_source="camera",
    )


def _record(wagon, kind, weight, *, orientation="", photo=True):
    record = WeighingRecord.objects.create(
        wagon=wagon, kind=kind, weight_kg=weight, source="scale", orientation=orientation
    )
    if photo:
        record.photo.save(f"{uuid4()}.jpg", ContentFile(JPEG), save=True)
    return record


def _unassigned(weight, *, vehicle_number="", wagon=None, photo=True):
    item = UnassignedWeighing.objects.create(
        weight_kg=weight,
        stable_weight_at=timezone.now() - timedelta(minutes=30),
        scale_number="truck",
        scale_age_seconds=Decimal("0.2"),
        camera="cam1",
        photo_request_id=uuid4(),
        vehicle_number=vehicle_number,
        wagon=wagon,
    )
    if photo:
        item.photo.save(f"{item.photo_request_id}.jpg", ContentFile(JPEG), save=True)
    return item


def _sample(kind, record_id, label, source, *, captured_at=None, **fields):
    """Строка датасета напрямую — для фильтров исходная запись не нужна."""
    return VehicleOrientationSample.objects.create(
        record_kind=kind,
        record_id=record_id,
        label=label,
        label_source=source,
        weight_kg=fields.pop("weight_kg", 4000),
        captured_at=captured_at or timezone.now(),
        **fields,
    )


def _sample_for(sample_or_record) -> VehicleOrientationSample:
    return VehicleOrientationSample.objects.get(
        record_kind=(
            VehicleOrientationSample.WEIGHING
            if isinstance(sample_or_record, WeighingRecord)
            else VehicleOrientationSample.UNASSIGNED
        ),
        record_id=sample_or_record.pk,
    )


def test_list_shows_signed_photo_links_and_trip_numbers(auth_client, viewer):
    trip = _trip("854ANB13")
    entry = _record(trip, "gross", 3880)
    named = _unassigned(3900, vehicle_number="506WKZ13")
    dataset.collect()
    # collect() пропускает кадры без файла; строка без фото — как после утери файла.
    blank = _unassigned(3950, photo=False)
    _sample(VehicleOrientationSample.UNASSIGNED, blank.pk, "front", "weight")

    response = auth_client(viewer).get(LIST_URL)

    assert response.status_code == 200, response.data
    rows = {row["sample_id"]: row for row in response.data}
    assert set(rows) == {f"weighing-{entry.pk}", f"unassigned-{named.pk}", f"unassigned-{blank.pk}"}
    weighing = rows[f"weighing-{entry.pk}"]
    assert weighing["vehicle_number"] == "854ANB13"
    assert weighing["wagon"] == trip.pk
    assert (weighing["label"], weighing["label_source"]) == ("front", "trip")
    assert weighing["photo_url"].startswith(f"/api/grain/photos/weighing/{entry.pk}/?token=")
    assert rows[f"unassigned-{named.pk}"]["vehicle_number"] == "506WKZ13"
    assert rows[f"unassigned-{named.pk}"]["wagon"] is None
    assert rows[f"unassigned-{named.pk}"]["photo_url"].startswith(
        f"/api/grain/photos/unassigned/{named.pk}/?token="
    )
    assert rows[f"unassigned-{blank.pk}"]["vehicle_number"] == ""
    assert rows[f"unassigned-{blank.pk}"]["photo_url"] is None
    assert weighing["reviewed_by_name"] is None
    assert weighing["sent_at"] is None

    photo = auth_client(viewer).get(weighing["photo_url"])
    assert photo.status_code == 200
    assert b"".join(photo.streaming_content) == JPEG


def test_unassigned_number_falls_back_to_the_assigned_trip(auth_client, viewer):
    trip = _trip("676VEA13")
    item = _unassigned(8760, wagon=trip)
    sample = _sample(VehicleOrientationSample.UNASSIGNED, item.pk, "rear", "weight")

    response = auth_client(viewer).get(f"{LIST_URL}{sample.pk}/")

    assert response.status_code == 200
    assert response.data["vehicle_number"] == "676VEA13"
    assert response.data["wagon"] == trip.pk


def test_list_is_ordered_by_capture_time_then_id(auth_client, viewer):
    now = timezone.now()
    older = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "weight", captured_at=now - timedelta(hours=2))
    newer = _sample(VehicleOrientationSample.WEIGHING, 2, "front", "weight", captured_at=now)
    same_time = _sample(VehicleOrientationSample.WEIGHING, 3, "rear", "weight", captured_at=now)

    response = auth_client(viewer).get(LIST_URL)

    assert [row["id"] for row in response.data] == [same_time.pk, newer.pk, older.pk]


@pytest.fixture
def filter_rows():
    now = timezone.now()
    return {
        "front_trip_sent": _sample(
            VehicleOrientationSample.WEIGHING, 1, "front", "trip",
            sent_at=now, captured_at=now,
        ),
        "rear_trip_conflict": _sample(
            VehicleOrientationSample.WEIGHING, 2, "rear", "trip",
            conflict=True, model_orientation="front", captured_at=now - timedelta(minutes=1),
        ),
        "front_weight_unsent": _sample(
            VehicleOrientationSample.UNASSIGNED, 3, "front", "weight",
            captured_at=now - timedelta(minutes=2),
        ),
        "rear_manual_excluded": _sample(
            VehicleOrientationSample.UNASSIGNED, 4, "rear", "manual",
            excluded=True, captured_at=now - timedelta(minutes=3),
        ),
    }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ["front_trip_sent", "rear_trip_conflict", "front_weight_unsent"]),
        ("label=front", ["front_trip_sent", "front_weight_unsent"]),
        ("label=rear", ["rear_trip_conflict"]),
        ("source=trip", ["front_trip_sent", "rear_trip_conflict"]),
        ("source=weight", ["front_weight_unsent"]),
        ("source=manual", []),
        ("kind=weighing", ["front_trip_sent", "rear_trip_conflict"]),
        ("kind=unassigned", ["front_weight_unsent"]),
        ("conflict=1", ["rear_trip_conflict"]),
        ("unsent=1", ["rear_trip_conflict", "front_weight_unsent"]),
        ("excluded=1", ["rear_manual_excluded"]),
        ("excluded=1&source=manual", ["rear_manual_excluded"]),
        ("excluded=0&label=rear", ["rear_trip_conflict"]),
    ],
)
def test_list_filters(auth_client, viewer, filter_rows, query, expected):
    response = auth_client(viewer).get(f"{LIST_URL}?{query}")

    assert response.status_code == 200, response.data
    assert [row["id"] for row in response.data] == [filter_rows[name].pk for name in expected]


@pytest.mark.parametrize(
    "query", ["label=side", "source=camera", "kind=photo", "conflict=yes", "excluded=true", "unsent=2"]
)
def test_invalid_filter_values_are_rejected(auth_client, viewer, query):
    response = auth_client(viewer).get(f"{LIST_URL}?{query}")

    assert response.status_code == 400
    assert response.data["code"] == "bad_filter"


def test_excluded_rows_are_hidden_from_the_list_but_still_retrievable(auth_client, viewer):
    kept = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")
    dropped = _sample(VehicleOrientationSample.WEIGHING, 2, "front", "trip", excluded=True)

    listed = auth_client(viewer).get(LIST_URL)
    detail = auth_client(viewer).get(f"{LIST_URL}{dropped.pk}/")

    assert [row["id"] for row in listed.data] == [kept.pk]
    assert detail.status_code == 200
    assert detail.data["excluded"] is True


def test_pagination_is_opt_in(auth_client, viewer):
    for record_id in range(1, 4):
        _sample(VehicleOrientationSample.WEIGHING, record_id, "front", "weight")

    flat = auth_client(viewer).get(LIST_URL)
    page = auth_client(viewer).get(f"{LIST_URL}?page=1&page_size=2")

    assert isinstance(flat.data, list) and len(flat.data) == 3
    assert page.data["count"] == 3
    assert len(page.data["results"]) == 2
    assert page.data["next"] is not None


def test_list_requires_view_permission(auth_client, user_with_perms):
    stranger = user_with_perms("orientation-stranger", codes=["grain.weigh"])

    assert auth_client(stranger).get(LIST_URL).status_code == 403


def test_label_action_needs_admin_and_marks_the_row_manual(auth_client, viewer, admin):
    trip = _trip()
    entry = _record(trip, "gross", 3880, orientation="rear")  # classifier disagreed
    dataset.collect()
    sample = _sample_for(entry)
    assert sample.conflict is True
    sample.sent_at = timezone.now()
    sample.save(update_fields=["sent_at"])

    forbidden = auth_client(viewer).post(
        f"{LIST_URL}{sample.pk}/label/", {"label": "rear"}, format="json"
    )
    response = auth_client(admin).post(
        f"{LIST_URL}{sample.pk}/label/", {"label": "rear"}, format="json"
    )

    assert forbidden.status_code == 403
    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "no-store"
    assert (response.data["label"], response.data["label_source"]) == ("rear", "manual")
    assert response.data["sent_at"] is None
    assert response.data["conflict"] is False
    assert response.data["reviewed_by_name"] == "orientation-admin"
    assert response.data["reviewed_at"] is not None
    assert response.data["vehicle_number"] == "854ANB13"
    assert response.data["photo_url"].startswith(f"/api/grain/photos/weighing/{entry.pk}/")
    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.sent_at) == ("rear", "manual", None)
    assert sample.reviewed_by == admin


@pytest.mark.parametrize("body", [{"label": "side"}, {"label": ""}, {}, {"label": None}])
def test_label_action_rejects_anything_but_front_or_rear(auth_client, admin, body):
    sample = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")

    response = auth_client(admin).post(f"{LIST_URL}{sample.pk}/label/", body, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "bad_label"
    sample.refresh_from_db()
    assert sample.label_source == "trip"


def test_exclude_action_drops_the_frame_and_hides_it(auth_client, viewer, admin):
    sample = _sample(
        VehicleOrientationSample.WEIGHING, 1, "front", "trip",
        sent_at=timezone.now(), conflict=True,
    )

    forbidden = auth_client(viewer).post(f"{LIST_URL}{sample.pk}/exclude/", {}, format="json")
    response = auth_client(admin).post(f"{LIST_URL}{sample.pk}/exclude/", {}, format="json")

    assert forbidden.status_code == 403
    assert response.status_code == 200, response.data
    assert response.data["excluded"] is True
    assert response.data["conflict"] is False
    assert response.data["reviewed_by_name"] == "orientation-admin"
    sample.refresh_from_db()
    assert (sample.excluded, sample.removal_pending) == (True, True)
    assert auth_client(viewer).get(LIST_URL).data == []
    assert [row["id"] for row in auth_client(viewer).get(f"{LIST_URL}?excluded=1").data] == [sample.pk]


def test_summary_counts_the_dataset_and_survives_a_camera_pc_outage(auth_client, viewer):
    now = timezone.now()
    _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip", sent_at=now)
    _sample(VehicleOrientationSample.WEIGHING, 2, "rear", "trip", conflict=True)
    _sample(VehicleOrientationSample.UNASSIGNED, 3, "front", "weight")
    _sample(VehicleOrientationSample.UNASSIGNED, 4, "rear", "manual", sent_at=now)
    _sample(VehicleOrientationSample.UNASSIGNED, 5, "rear", "manual", excluded=True)

    with patch.object(
        camera_ai, "vehicle_orientation_info", side_effect=camera_ai.AiUnavailable("down")
    ):
        response = auth_client(viewer).get(f"{LIST_URL}summary/")

    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "total": 4,
        "by_label": {"front": 2, "rear": 2},
        "by_source": {"trip": 2, "weight": 1, "manual": 1},
        "conflicts": 1,
        "excluded": 1,
        "unsent": 2,
        "camera_pc": None,
    }

    # An outage is remembered for a short while so polling tabs do not hammer the PC.
    with patch.object(camera_ai, "vehicle_orientation_info", return_value={"enabled": True}) as info_call:
        assert auth_client(viewer).get(f"{LIST_URL}summary/").data["camera_pc"] is None
    info_call.assert_not_called()
    cache.delete(grain_views.ORIENTATION_PC_CACHE_KEY)

    info = {"enabled": True, "dataset": {"front": 60, "rear": 62}, "training": {"status": "promoted"}}
    with patch.object(camera_ai, "vehicle_orientation_info", return_value=info) as info_call:
        response = auth_client(viewer).get(f"{LIST_URL}summary/")
        assert response.data["camera_pc"] == info
        # Cached: the second poll within 30 s does not touch the PC again.
        assert auth_client(viewer).get(f"{LIST_URL}summary/").data["camera_pc"] == info
    assert info_call.call_count == 1
    cache.delete(grain_views.ORIENTATION_PC_CACHE_KEY)

    with patch.object(
        camera_ai, "vehicle_orientation_info", side_effect=camera_ai.AiError(500, "boom", {})
    ):
        assert auth_client(viewer).get(f"{LIST_URL}summary/").data["camera_pc"] is None


def test_summary_requires_view_permission_only(auth_client, user_with_perms):
    stranger = user_with_perms("orientation-summary-stranger", codes=["grain.weigh"])

    with patch.object(camera_ai, "vehicle_orientation_info", return_value={}):
        assert auth_client(stranger).get(f"{LIST_URL}summary/").status_code == 403


def test_list_query_count_does_not_grow_with_rows(auth_client, viewer):
    def _add_samples(count):
        for index in range(count):
            trip = _trip(f"{100 + index}ABC13")
            _record(trip, "gross", 3880)
            _unassigned(3900, vehicle_number=f"{200 + index}XYZ13")
        dataset.collect()

    _add_samples(1)
    with CaptureQueriesContext(connection) as small:
        response = auth_client(viewer).get(LIST_URL)
    assert response.status_code == 200
    assert len(response.data) == 2

    _add_samples(4)
    with CaptureQueriesContext(connection) as large:
        response = auth_client(viewer).get(LIST_URL)

    assert response.status_code == 200
    assert len(response.data) == 10
    assert all(row["photo_url"] and row["vehicle_number"] for row in response.data)
    # План запросов постоянный: строка не ходит в базу за фото и номером.
    assert len(large) == len(small), f"N+1: {len(small)} → {len(large)} запросов"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", ""),
        ("get", "summary/"),
        ("get", "{pk}/"),
        ("post", "{pk}/label/"),
        ("post", "{pk}/exclude/"),
    ],
)
def test_every_route_requires_authentication(api_client, method, path):
    sample = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")

    response = getattr(api_client, method)(
        f"{LIST_URL}{path.format(pk=sample.pk)}", {"label": "rear"}, format="json"
    )

    assert response.status_code == 401
    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.excluded) == ("front", "trip", False)
