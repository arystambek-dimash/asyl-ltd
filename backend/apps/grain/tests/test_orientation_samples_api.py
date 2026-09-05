"""API разметки датасета ориентации: доступ владельца, список, фильтры, метки, сводка, очистка."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import call, patch
from uuid import uuid4

import pytest
from apps.cameras import ai as camera_ai
from apps.grain import orientation_dataset as dataset
from apps.grain import statuses as st
from apps.grain import views as grain_views
from apps.grain.models import (
    UnassignedWeighing,
    VehicleOrientationDatasetState,
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
ADMIN_URL = "/admin/grain/vehicleorientationsample/"
ROUTES = [
    ("get", ""),
    ("get", "summary/"),
    ("get", "{pk}/"),
    ("post", "{pk}/label/"),
    ("post", "{pk}/exclude/"),
    ("post", "purge/"),
]


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


@pytest.fixture
def root(user_with_perms):
    """Владелец — единственный, кому открыт датасет."""
    user = user_with_perms("orientation-owner", codes=[])
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    return user


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


def _call(client, method, url):
    """GET без тела; POST — с телом, которое приняли бы label/ и purge/."""
    if method == "get":
        return client.get(url)
    return client.post(url, {"label": "rear", "older_than_days": None}, format="json")


def test_list_shows_signed_photo_links_and_trip_numbers(auth_client, root):
    trip = _trip("854ANB13")
    entry = _record(trip, "gross", 3880)
    named = _unassigned(3900, vehicle_number="506WKZ13")
    dataset.collect()
    # collect() пропускает кадры без файла; строка без фото — как после утери файла.
    blank = _unassigned(3950, photo=False)
    _sample(VehicleOrientationSample.UNASSIGNED, blank.pk, "front", "weight")

    response = auth_client(root).get(LIST_URL)

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

    photo = auth_client(root).get(weighing["photo_url"])
    assert photo.status_code == 200
    assert b"".join(photo.streaming_content) == JPEG


def test_unassigned_number_falls_back_to_the_assigned_trip(auth_client, root):
    trip = _trip("676VEA13")
    item = _unassigned(8760, wagon=trip)
    sample = _sample(VehicleOrientationSample.UNASSIGNED, item.pk, "rear", "weight")

    response = auth_client(root).get(f"{LIST_URL}{sample.pk}/")

    assert response.status_code == 200
    assert response.data["vehicle_number"] == "676VEA13"
    assert response.data["wagon"] == trip.pk


def test_list_is_ordered_by_capture_time_then_id(auth_client, root):
    now = timezone.now()
    older = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "weight", captured_at=now - timedelta(hours=2))
    newer = _sample(VehicleOrientationSample.WEIGHING, 2, "front", "weight", captured_at=now)
    same_time = _sample(VehicleOrientationSample.WEIGHING, 3, "rear", "weight", captured_at=now)

    response = auth_client(root).get(LIST_URL)

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
def test_list_filters(auth_client, root, filter_rows, query, expected):
    response = auth_client(root).get(f"{LIST_URL}?{query}")

    assert response.status_code == 200, response.data
    assert [row["id"] for row in response.data] == [filter_rows[name].pk for name in expected]


@pytest.mark.parametrize(
    "query", ["label=side", "source=camera", "kind=photo", "conflict=yes", "excluded=true", "unsent=2"]
)
def test_invalid_filter_values_are_rejected(auth_client, root, query):
    response = auth_client(root).get(f"{LIST_URL}?{query}")

    assert response.status_code == 400
    assert response.data["code"] == "bad_filter"


def test_excluded_rows_are_hidden_from_the_list_but_still_retrievable(auth_client, root):
    kept = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")
    dropped = _sample(VehicleOrientationSample.WEIGHING, 2, "front", "trip", excluded=True)

    listed = auth_client(root).get(LIST_URL)
    detail = auth_client(root).get(f"{LIST_URL}{dropped.pk}/")

    assert [row["id"] for row in listed.data] == [kept.pk]
    assert detail.status_code == 200
    assert detail.data["excluded"] is True


def test_pagination_is_opt_in(auth_client, root):
    for record_id in range(1, 4):
        _sample(VehicleOrientationSample.WEIGHING, record_id, "front", "weight")

    flat = auth_client(root).get(LIST_URL)
    page = auth_client(root).get(f"{LIST_URL}?page=1&page_size=2")

    assert isinstance(flat.data, list) and len(flat.data) == 3
    assert page.data["count"] == 3
    assert len(page.data["results"]) == 2
    assert page.data["next"] is not None


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_is_superuser_only(auth_client, viewer, admin, method, path):
    """Права grain.view/grain.admin не открывают датасет: только владелец."""
    sample = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")
    url = f"{LIST_URL}{path.format(pk=sample.pk)}"

    with (
        patch.object(camera_ai, "delete_orientation_sample") as delete,
        patch.object(camera_ai, "clear_orientation_samples") as clear,
    ):
        for user in (viewer, admin):
            response = _call(auth_client(user), method, url)
            assert response.status_code == 403, (user.username, response.data)

    delete.assert_not_called()
    clear.assert_not_called()
    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.excluded) == ("front", "trip", False)


def test_label_action_marks_the_row_manual(auth_client, root):
    trip = _trip()
    entry = _record(trip, "gross", 3880, orientation="rear")  # classifier disagreed
    dataset.collect()
    sample = _sample_for(entry)
    assert sample.conflict is True
    sample.sent_at = sample.delivered_at = timezone.now()
    sample.save(update_fields=["sent_at", "delivered_at"])

    response = auth_client(root).post(
        f"{LIST_URL}{sample.pk}/label/", {"label": "rear"}, format="json"
    )

    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "no-store"
    assert (response.data["label"], response.data["label_source"]) == ("rear", "manual")
    assert response.data["sent_at"] is None
    assert response.data["delivered_at"] is not None  # the PC still holds the old copy
    assert response.data["conflict"] is False
    assert response.data["reviewed_by_name"] == "orientation-owner"
    assert response.data["reviewed_at"] is not None
    assert response.data["vehicle_number"] == "854ANB13"
    assert response.data["photo_url"].startswith(f"/api/grain/photos/weighing/{entry.pk}/")
    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.sent_at) == ("rear", "manual", None)
    assert sample.reviewed_by == root


@pytest.mark.parametrize("body", [{"label": "side"}, {"label": ""}, {}, {"label": None}])
def test_label_action_rejects_anything_but_front_or_rear(auth_client, root, body):
    sample = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")

    response = auth_client(root).post(f"{LIST_URL}{sample.pk}/label/", body, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "bad_label"
    sample.refresh_from_db()
    assert sample.label_source == "trip"


def test_exclude_action_drops_the_frame_and_hides_it(auth_client, root):
    now = timezone.now()
    sample = _sample(
        VehicleOrientationSample.WEIGHING, 1, "front", "trip",
        sent_at=now, delivered_at=now, conflict=True,
    )

    response = auth_client(root).post(f"{LIST_URL}{sample.pk}/exclude/", {}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["excluded"] is True
    assert response.data["conflict"] is False
    assert response.data["reviewed_by_name"] == "orientation-owner"
    sample.refresh_from_db()
    assert (sample.excluded, sample.removal_pending) == (True, True)
    assert auth_client(root).get(LIST_URL).data == []
    assert [row["id"] for row in auth_client(root).get(f"{LIST_URL}?excluded=1").data] == [sample.pk]


def test_summary_counts_the_dataset_and_survives_a_camera_pc_outage(auth_client, root):
    now = timezone.now()
    _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip", sent_at=now)
    _sample(VehicleOrientationSample.WEIGHING, 2, "rear", "trip", conflict=True)
    _sample(VehicleOrientationSample.UNASSIGNED, 3, "front", "weight")
    _sample(VehicleOrientationSample.UNASSIGNED, 4, "rear", "manual", sent_at=now)
    _sample(VehicleOrientationSample.UNASSIGNED, 5, "rear", "manual", excluded=True)

    with patch.object(
        camera_ai, "vehicle_orientation_info", side_effect=camera_ai.AiUnavailable("down")
    ):
        response = auth_client(root).get(f"{LIST_URL}summary/")

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
        assert auth_client(root).get(f"{LIST_URL}summary/").data["camera_pc"] is None
    info_call.assert_not_called()
    cache.delete(grain_views.ORIENTATION_PC_CACHE_KEY)

    info = {"enabled": True, "dataset": {"front": 60, "rear": 62}, "training": {"status": "promoted"}}
    with patch.object(camera_ai, "vehicle_orientation_info", return_value=info) as info_call:
        response = auth_client(root).get(f"{LIST_URL}summary/")
        assert response.data["camera_pc"] == info
        # Cached: the second poll within 30 s does not touch the PC again.
        assert auth_client(root).get(f"{LIST_URL}summary/").data["camera_pc"] == info
    assert info_call.call_count == 1
    cache.delete(grain_views.ORIENTATION_PC_CACHE_KEY)

    with patch.object(
        camera_ai, "vehicle_orientation_info", side_effect=camera_ai.AiError(500, "boom", {})
    ):
        assert auth_client(root).get(f"{LIST_URL}summary/").data["camera_pc"] is None


def test_list_query_count_does_not_grow_with_rows(auth_client, root):
    def _add_samples(count):
        for index in range(count):
            trip = _trip(f"{100 + index}ABC13")
            _record(trip, "gross", 3880)
            _unassigned(3900, vehicle_number=f"{200 + index}XYZ13")
        dataset.collect()

    _add_samples(1)
    with CaptureQueriesContext(connection) as small:
        response = auth_client(root).get(LIST_URL)
    assert response.status_code == 200
    assert len(response.data) == 2

    _add_samples(4)
    with CaptureQueriesContext(connection) as large:
        response = auth_client(root).get(LIST_URL)

    assert response.status_code == 200
    assert len(response.data) == 10
    assert all(row["photo_url"] and row["vehicle_number"] for row in response.data)
    # План запросов постоянный: строка не ходит в базу за фото и номером.
    assert len(large) == len(small), f"N+1: {len(small)} → {len(large)} запросов"


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_requires_authentication(api_client, method, path):
    sample = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")

    with patch.object(camera_ai, "clear_orientation_samples") as clear:
        response = _call(api_client, method, f"{LIST_URL}{path.format(pk=sample.pk)}")

    assert response.status_code == 401
    clear.assert_not_called()
    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.excluded) == ("front", "trip", False)


# --- Очистка датасета -------------------------------------------------------


def test_purge_all_clears_camera_pc_in_one_call_and_deletes_every_row(auth_client, root):
    trip = _trip()
    entry = _record(trip, "gross", 3880)
    dataset.collect()
    now = timezone.now()
    _sample(VehicleOrientationSample.UNASSIGNED, 7, "rear", "weight", sent_at=now)
    _sample(
        VehicleOrientationSample.UNASSIGNED, 8, "front", "manual",
        excluded=True, removal_pending=True,
    )

    with (
        patch.object(camera_ai, "clear_orientation_samples", return_value=3) as clear,
        patch.object(camera_ai, "delete_orientation_sample") as delete,
    ):
        response = auth_client(root).post(
            f"{LIST_URL}purge/", {"older_than_days": None}, format="json"
        )

    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "deleted": 3, "removed_from_pc": 3, "pc_unavailable": False, "remaining": 0
    }
    clear.assert_called_once_with()
    delete.assert_not_called()
    assert VehicleOrientationSample.objects.count() == 0
    # Свидетельство рейса остаётся: взвешивание и его фото не тронуты.
    entry.refresh_from_db()
    assert entry.photo and entry.photo.storage.exists(entry.photo.name)
    assert auth_client(root).get(LIST_URL).data == []
    # Водораздел сбора сдвинут: ночной сбор не воскресит стёртый кадр из того же фото.
    assert dataset.collect()["created"] == 0
    assert auth_client(root).get(f"{LIST_URL}summary/").data["total"] == 0


def test_purge_older_than_days_deletes_only_old_rows_one_by_one(auth_client, root):
    now = timezone.now()
    old_sent = _sample(
        VehicleOrientationSample.WEIGHING, 1, "front", "trip",
        sent_at=now, delivered_at=now, captured_at=now - timedelta(days=40),
    )
    old_unsent = _sample(
        VehicleOrientationSample.WEIGHING, 2, "rear", "weight",
        captured_at=now - timedelta(days=31),
    )
    fresh_sent = _sample(
        VehicleOrientationSample.WEIGHING, 3, "front", "trip",
        sent_at=now, delivered_at=now, captured_at=now - timedelta(days=29),
    )
    trip = _trip()
    old_record = _record(trip, "gross", 3880)
    WeighingRecord.objects.filter(pk=old_record.pk).update(created_at=now - timedelta(days=45))

    with (
        patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete,
        patch.object(camera_ai, "clear_orientation_samples") as clear,
    ):
        response = auth_client(root).post(
            f"{LIST_URL}purge/", {"older_than_days": 30}, format="json"
        )

    assert response.status_code == 200, response.data
    assert response.data == {
        "deleted": 2, "removed_from_pc": 1, "pc_unavailable": False, "remaining": 0
    }
    # Кадр, которого ПК не получал, удаляется без запроса к нему.
    delete.assert_called_once_with(old_sent.sample_id)
    clear.assert_not_called()
    assert not VehicleOrientationSample.objects.filter(pk__in=[old_sent.pk, old_unsent.pk]).exists()
    assert list(VehicleOrientationSample.objects.values_list("pk", flat=True)) == [fresh_sent.pk]
    # Стёртый период закрыт: взвешивание 45-дневной давности ночью не собирается.
    assert dataset.collect()["created"] == 0
    assert VehicleOrientationDatasetState.load().collect_since >= now - timedelta(days=30)


def test_purge_answers_one_batch_at_a_time_until_nothing_remains(auth_client, root):
    now = timezone.now()
    VehicleOrientationSample.objects.bulk_create(
        VehicleOrientationSample(
            record_kind=VehicleOrientationSample.WEIGHING,
            record_id=record_id,
            label="front",
            label_source="weight",
            weight_kg=4000,
            captured_at=now - timedelta(days=40),
        )
        for record_id in range(1, dataset.PURGE_BATCH + 2)
    )
    client = auth_client(root)

    with patch.object(camera_ai, "delete_orientation_sample") as delete:
        first = client.post(f"{LIST_URL}purge/", {"older_than_days": 30}, format="json")
        second = client.post(f"{LIST_URL}purge/", {"older_than_days": 30}, format="json")

    assert first.status_code == 200, first.data
    assert first.data == {
        "deleted": dataset.PURGE_BATCH, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 1
    }
    assert second.data == {"deleted": 1, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 0}
    delete.assert_not_called()
    assert VehicleOrientationSample.objects.count() == 0

    # «Всё» на старой прошивке ПК без массового удаления: тоже пакетами.
    for record_id in range(1, 4):
        _sample(VehicleOrientationSample.WEIGHING, record_id, "front", "trip", sent_at=now, delivered_at=now)
    with (
        patch.object(
            camera_ai, "clear_orientation_samples", side_effect=camera_ai.AiError(404, "no route", {})
        ),
        patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete,
        patch.object(dataset, "PURGE_BATCH", 2),
    ):
        first = client.post(f"{LIST_URL}purge/", {"older_than_days": None}, format="json")
        second = client.post(f"{LIST_URL}purge/", {"older_than_days": None}, format="json")
    assert first.data == {"deleted": 2, "removed_from_pc": 2, "pc_unavailable": False, "remaining": 1}
    assert second.data == {"deleted": 1, "removed_from_pc": 1, "pc_unavailable": False, "remaining": 0}
    assert delete.call_count == 3
    assert VehicleOrientationSample.objects.count() == 0


def test_purge_keeps_rows_camera_pc_could_not_forget_for_the_nightly_job(auth_client, root):
    now = timezone.now()
    first = _sample(
        VehicleOrientationSample.WEIGHING, 1, "front", "trip",
        sent_at=now, delivered_at=now, conflict=True,
    )
    second = _sample(
        VehicleOrientationSample.WEIGHING, 2, "rear", "trip", sent_at=now, delivered_at=now
    )
    unsent = _sample(VehicleOrientationSample.WEIGHING, 3, "front", "weight")

    with (
        patch.object(
            camera_ai, "clear_orientation_samples", side_effect=camera_ai.AiUnavailable("down")
        ),
        patch.object(
            camera_ai, "delete_orientation_sample", side_effect=camera_ai.AiUnavailable("timed out")
        ) as delete,
    ):
        response = auth_client(root).post(
            f"{LIST_URL}purge/", {"older_than_days": None}, format="json"
        )

    assert response.status_code == 200, response.data
    assert response.data == {
        "deleted": 1, "removed_from_pc": 0, "pc_unavailable": True, "remaining": 0
    }
    assert delete.call_count == 1  # stop at the first transport failure
    assert not VehicleOrientationSample.objects.filter(pk=unsent.pk).exists()
    for sample in (first, second):
        sample.refresh_from_db()
        assert (sample.excluded, sample.removal_pending, sample.conflict) == (True, True, False)
    assert "timed out" in first.last_error
    assert second.last_error == ""
    # Оставшиеся копии заберёт ночной экспорт, когда ПК вернётся.
    with patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete:
        assert dataset.export_removals(limit=10) == {"removed": 2, "remove_failed": 0}
    assert delete.call_args_list == [call(first.sample_id), call(second.sample_id)]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"older_than_days": 0},
        {"older_than_days": -1},
        {"older_than_days": "30"},
        {"older_than_days": True},
        {"older_than_days": 1.5},
        [],
    ],
)
def test_purge_rejects_bad_bodies_without_touching_anything(auth_client, root, body):
    sample = _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip", sent_at=timezone.now())

    with (
        patch.object(camera_ai, "clear_orientation_samples") as clear,
        patch.object(camera_ai, "delete_orientation_sample") as delete,
    ):
        response = auth_client(root).post(f"{LIST_URL}purge/", body, format="json")

    assert response.status_code == 400, response.data
    assert response.data["code"] == "bad_purge"
    clear.assert_not_called()
    delete.assert_not_called()
    assert VehicleOrientationSample.objects.filter(pk=sample.pk).exists()


# --- Django admin -------------------------------------------------------------


def test_admin_changelist_shows_thumbnails_and_dataset_actions(admin_client):
    trip = _trip()
    entry = _record(trip, "gross", 3880)
    dataset.collect()
    blank = _unassigned(3950, photo=False)
    _sample(VehicleOrientationSample.UNASSIGNED, blank.pk, "front", "weight")

    response = admin_client.get(ADMIN_URL)

    assert response.status_code == 200
    html = response.content.decode()
    photo_link = f"/api/grain/photos/weighing/{entry.pk}/?token="
    assert f'<img src="{photo_link}' in html
    assert f'<a href="{photo_link}' in html
    assert f"weighing-{entry.pk}" in html
    assert f"unassigned-{blank.pk}" in html
    for label in ("Метка: передом", "Метка: задом", "Исключить", "Исключить и удалить с ПК"):
        assert label in html
    # Обычное удаление обошло бы Camera-PC: его в списке действий нет.
    assert "delete_selected" not in html
    detail = admin_client.get(f"{ADMIN_URL}{_sample_for(entry).pk}/change/")
    assert detail.status_code == 200
    assert f'<img src="{photo_link}' in detail.content.decode()


def test_admin_hides_the_dataset_from_staff_who_are_not_the_owner(client, user_with_perms):
    staff = user_with_perms("orientation-staff", codes=["grain.view", "grain.admin"])
    staff.is_staff = True
    staff.save(update_fields=["is_staff"])
    _sample(VehicleOrientationSample.WEIGHING, 1, "front", "trip")
    client.force_login(staff)

    assert client.get(ADMIN_URL).status_code == 403
    assert client.get("/admin/grain/").status_code == 404


def test_admin_actions_go_through_the_dataset_services(admin_client, admin_user):
    now = timezone.now()
    relabelled = _sample(
        VehicleOrientationSample.WEIGHING, 1, "front", "trip",
        sent_at=now, delivered_at=now, conflict=True,
    )
    excluded = _sample(
        VehicleOrientationSample.WEIGHING, 2, "front", "trip", sent_at=now, delivered_at=now
    )
    never_sent = _sample(VehicleOrientationSample.WEIGHING, 3, "front", "weight")
    on_pc = _sample(VehicleOrientationSample.WEIGHING, 4, "rear", "weight", sent_at=now, delivered_at=now)

    response = admin_client.post(
        ADMIN_URL, {"action": "mark_rear", "_selected_action": [relabelled.pk], "index": 0}
    )
    assert response.status_code == 302
    relabelled.refresh_from_db()
    assert (relabelled.label, relabelled.label_source, relabelled.conflict) == ("rear", "manual", False)
    assert (relabelled.sent_at, relabelled.delivered_at) == (None, now)
    assert relabelled.reviewed_by == admin_user

    admin_client.post(
        ADMIN_URL, {"action": "exclude_samples", "_selected_action": [excluded.pk], "index": 0}
    )
    excluded.refresh_from_db()
    assert (excluded.excluded, excluded.removal_pending) == (True, True)

    # «Исключить и удалить с ПК»: строки остаются исключёнными (иначе ночной
    # сбор воссоздал бы их), а ПК забывает доставленные копии сразу.
    with patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete:
        response = admin_client.post(
            ADMIN_URL,
            {"action": "exclude_and_remove", "_selected_action": [on_pc.pk, never_sent.pk], "index": 0},
            follow=True,
        )
    assert response.status_code == 200
    # One removal pass sized to the selection; the earlier pending row rides along.
    assert delete.call_args_list == [call(excluded.sample_id), call(on_pc.sample_id)]
    assert VehicleOrientationSample.objects.count() == 4
    for sample in (on_pc, never_sent):
        sample.refresh_from_db()
        assert sample.excluded is True
        assert sample.reviewed_by == admin_user
        assert (sample.removal_pending, sample.delivered_at) == (False, None)
    assert "Исключено из датасета: 2, стёрто с Camera-PC: 2." in [
        str(message) for message in response.context["messages"]
    ]
    assert dataset.collect()["created"] == 0  # excluded rows are not re-created

    with patch.object(
        camera_ai, "delete_orientation_sample", side_effect=camera_ai.AiUnavailable("down")
    ):
        response = admin_client.post(
            ADMIN_URL,
            {"action": "exclude_and_remove", "_selected_action": [relabelled.pk], "index": 0},
            follow=True,
        )
    messages = [str(message) for message in response.context["messages"]]
    assert any("Не удалось стереть: 1" in message for message in messages)
    relabelled.refresh_from_db()
    assert (relabelled.excluded, relabelled.removal_pending, relabelled.delivered_at) == (True, True, now)
