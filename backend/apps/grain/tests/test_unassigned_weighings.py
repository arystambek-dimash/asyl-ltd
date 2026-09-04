"""API around plate-less automatic weighings: photos, numbers, assignment."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from apps.grain import statuses as st
from apps.grain.models import UnassignedWeighing, Wagon, WeighingRecord
from apps.grain.photos import photo_token
from django.core.files.base import ContentFile
from django.utils import timezone

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 32


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    return tmp_path


def _passage(number="", status=st.ARRIVED, entry=None):
    return Wagon.objects.create(
        number=number,
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=status,
        arrived_at=timezone.now() - timedelta(minutes=5),
        gross_weight_kg=entry,
        number_source="camera",
    )


def _unassigned(weight=30_000, with_photo=True):
    item = UnassignedWeighing.objects.create(
        weight_kg=weight,
        stable_weight_at=timezone.now() - timedelta(seconds=30),
        scale_number="truck",
        scale_age_seconds=Decimal("0.200"),
        scale_updated_at="2026-09-04T10:00:00Z",
        camera="cam1",
        photo_request_id=uuid4(),
        reason="open_passages_exist",
    )
    if with_photo:
        item.photo.save(f"{item.photo_request_id}.jpg", ContentFile(JPEG), save=True)
    return item


def test_operator_fills_in_the_number_of_a_blank_passage(auth_client, user_with_perms):
    operator = user_with_perms("passage-number", codes=["grain.arrive", "grain.view"])
    wagon = _passage(status=st.AT_SILO, entry=12_000)

    response = auth_client(operator).patch(
        f"/api/grain/wagons/{wagon.pk}/number/",
        {"number": " 465 bds 13 "},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["number"] == "465BDS13"
    assert response.data["number_source"] == "manual"
    wagon.refresh_from_db()
    assert wagon.number == "465BDS13"


def test_number_change_rejects_a_plate_already_on_site(auth_client, user_with_perms):
    operator = user_with_perms("passage-number-dup", codes=["grain.arrive"])
    _passage(number="465BDS13", status=st.AT_SILO, entry=12_000)
    wagon = _passage(status=st.AT_SILO, entry=13_000)

    response = auth_client(operator).patch(
        f"/api/grain/wagons/{wagon.pk}/number/",
        {"number": "465BDS13"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "passage_already_on_site"


def test_number_change_requires_arrive_permission(auth_client, user_with_perms):
    viewer = user_with_perms("passage-number-viewer", codes=["grain.view"])
    wagon = _passage(status=st.AT_SILO, entry=12_000)

    response = auth_client(viewer).patch(
        f"/api/grain/wagons/{wagon.pk}/number/",
        {"number": "465BDS13"},
        format="json",
    )

    assert response.status_code == 403


def test_open_unassigned_weighings_are_listed_with_signed_photo_links(
    auth_client, user_with_perms
):
    viewer = user_with_perms("unassigned-viewer", codes=["grain.view"])
    item = _unassigned()
    _unassigned(with_photo=False)
    UnassignedWeighing.objects.filter(pk=item.pk).update(status=UnassignedWeighing.OPEN)

    response = auth_client(viewer).get("/api/grain/unassigned-weighings/")

    assert response.status_code == 200
    rows = response.data
    assert [row["weight_kg"] for row in rows] == [30_000, 30_000]
    photo_url = next(row["photo_url"] for row in rows if row["id"] == item.pk)
    assert photo_url.startswith(f"/api/grain/photos/unassigned/{item.pk}/?token=")
    assert next(row["photo_url"] for row in rows if row["id"] != item.pk) is None

    photo = auth_client(viewer).get(photo_url)
    assert photo.status_code == 200
    assert photo["Content-Type"] == "image/jpeg"
    assert b"".join(photo.streaming_content) == JPEG


def test_photo_link_rejects_wrong_token_and_wrong_kind(api_client):
    item = _unassigned()
    wagon = _passage(status=st.AT_SILO, entry=12_000)
    weighing = WeighingRecord.objects.create(
        wagon=wagon, kind="gross", weight_kg=12_000, source="scale"
    )
    weighing.photo.save("w.jpg", ContentFile(JPEG), save=True)

    assert api_client.get(f"/api/grain/photos/unassigned/{item.pk}/?token=bad").status_code == 404
    swapped = photo_token("weighing", weighing.pk)
    assert (
        api_client.get(f"/api/grain/photos/unassigned/{item.pk}/?token={swapped}").status_code
        == 404
    )
    good = photo_token("weighing", weighing.pk)
    assert api_client.get(f"/api/grain/photos/weighing/{weighing.pk}/?token={good}").status_code == 200


def test_assigning_to_a_loaded_passage_records_its_exit_and_moves_the_photo(
    auth_client, user_with_perms
):
    operator = user_with_perms("unassigned-assign", codes=["grain.weigh"])
    wagon = _passage(number="465BDS13", status=st.AT_SILO, entry=12_000)
    item = _unassigned(weight=30_000)

    response = auth_client(operator).post(
        f"/api/grain/unassigned-weighings/{item.pk}/assign/",
        {"wagon": wagon.pk},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert (response.data["status"], response.data["action"]) == ("assigned", "exit")
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    assert wagon.exit_weight_kg == 30_000
    assert wagon.net_weight_kg == 18_000
    exit_weighing = WeighingRecord.objects.get(wagon=wagon, kind="tare")
    assert exit_weighing.source == "scale"
    assert exit_weighing.photo_request_id == item.photo_request_id
    assert exit_weighing.photo.name == item.photo.name
    detail = auth_client(user_with_perms("unassigned-view", codes=["grain.view"])).get(
        f"/api/grain/wagons/{wagon.pk}/"
    )
    assert detail.data["exit_photo_url"].startswith(
        f"/api/grain/photos/weighing/{exit_weighing.pk}/?token="
    )
    assert detail.data["entry_photo_url"] is None


def test_assigning_to_an_arrived_passage_records_its_entry(auth_client, user_with_perms):
    operator = user_with_perms("unassigned-entry", codes=["grain.weigh"])
    wagon = _passage(number="465BDS13", status=st.ARRIVED)
    item = _unassigned(weight=12_000, with_photo=False)

    response = auth_client(operator).post(
        f"/api/grain/unassigned-weighings/{item.pk}/assign/",
        {"wagon": wagon.pk},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["action"] == "entry"
    wagon.refresh_from_db()
    assert wagon.status == st.AT_SILO
    assert wagon.entry_weight_kg == 12_000


def test_assigning_to_a_passage_that_is_not_waiting_is_rejected(auth_client, user_with_perms):
    operator = user_with_perms("unassigned-reject", codes=["grain.weigh"])
    wagon = _passage(number="465BDS13", status=st.COMPLETED, entry=12_000)
    item = _unassigned(with_photo=False)

    response = auth_client(operator).post(
        f"/api/grain/unassigned-weighings/{item.pk}/assign/",
        {"wagon": wagon.pk},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "wagon_not_awaiting_weight"
    item.refresh_from_db()
    assert item.status == UnassignedWeighing.OPEN


def test_creating_a_passage_from_an_unassigned_weight_uses_it_as_the_entry(
    auth_client, user_with_perms
):
    operator = user_with_perms("unassigned-create", codes=["grain.weigh"])
    item = _unassigned(weight=1_800)

    response = auth_client(operator).post(
        f"/api/grain/unassigned-weighings/{item.pk}/create-passage/",
        {"number": "506wkz13", "cargo_name": ""},
        format="json",
    )

    assert response.status_code == 200, response.data
    wagon = Wagon.objects.get(pk=response.data["wagon"])
    assert response.data["action"] == "entry"
    assert wagon.number == "506WKZ13"
    assert wagon.cargo_name == "Отруби"
    assert wagon.status == st.AT_SILO
    assert wagon.entry_weight_kg == 1_800
    assert WeighingRecord.objects.get(wagon=wagon).photo.name == item.photo.name


def test_discarding_an_unassigned_weight_is_audited_and_final(auth_client, user_with_perms):
    operator = user_with_perms("unassigned-discard", codes=["grain.weigh"])
    item = _unassigned(with_photo=False)

    response = auth_client(operator).post(
        f"/api/grain/unassigned-weighings/{item.pk}/discard/",
        {"reason": "тестовый заезд"},
        format="json",
    )
    again = auth_client(operator).post(
        f"/api/grain/unassigned-weighings/{item.pk}/discard/",
        {"reason": "ещё раз"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "discarded"
    assert again.status_code == 400
    assert again.data["code"] == "unassigned_weighing_resolved"
    listed = auth_client(operator).get("/api/grain/unassigned-weighings/")
    assert listed.status_code == 403  # weigh alone does not grant grain.view


def test_unassigned_mutations_require_weigh_permission(auth_client, user_with_perms):
    viewer = user_with_perms("unassigned-noweigh", codes=["grain.view"])
    item = _unassigned(with_photo=False)

    response = auth_client(viewer).post(
        f"/api/grain/unassigned-weighings/{item.pk}/discard/",
        {},
        format="json",
    )

    assert response.status_code == 403
