from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.grain import historical_tare, services, statuses as st
from apps.grain.models import Wagon, WeighingRecord, UnassignedWeighing

pytestmark = pytest.mark.django_db


@pytest.fixture
def case(settings, user_with_perms):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    user = user_with_perms("tare-operator", codes=["grain.weigh", "grain.view"])
    source_trip = Wagon.objects.create(direction="passage", workflow="simple", number="123ABC13", status=st.COMPLETED, cargo_name="Отруби", gross_weight_kg=4000, tare_weight_kg=9000)
    source = WeighingRecord.objects.create(wagon=source_trip, kind="gross", source="scale", orientation="front", weight_kg=4000, photo="grain/old.jpg")
    WeighingRecord.objects.filter(pk=source.pk).update(created_at=timezone.now()-timedelta(days=1))
    source.refresh_from_db()
    item = UnassignedWeighing.objects.create(weight_kg=9200, stable_weight_at=timezone.now()-timedelta(minutes=1), orientation="rear", photo="grain/exit.jpg", photo_request_id=uuid4(), vehicle_number="123ABC13")
    return user, source_trip, source, item


def complete(case):
    user, _, source, item = case
    return historical_tare.complete(item, user, reference_record=source.pk, number="123ABC13", reason="Подтверждена прежняя тара")


def test_historical_exit_retains_original_trip_and_provenance(case):
    user, old_trip, source, item = case
    with patch("apps.grain.scale.read_truck_scale", side_effect=AssertionError("No hardware")):
        result = complete(case)
    wagon = result.wagon
    assert wagon.status == st.COMPLETED
    assert (wagon.gross_weight_kg, wagon.tare_weight_kg, wagon.net_weight_kg) == (4000,9200,5200)
    assert wagon.exited_at == item.stable_weight_at
    assert wagon.silo_arrived_at is None
    reference = wagon.weighings.get(kind="gross")
    assert reference.source == "historical" and reference.reference_record_id == source.pk
    assert reference.photo.name == source.photo.name
    assert wagon.weighings.get(kind="tare").photo.name == item.photo.name
    old_trip.refresh_from_db()
    assert old_trip.status == st.COMPLETED and old_trip.tare_weight_kg == 9000
    assert source.wagon_id == old_trip.pk
    complete(case)
    assert Wagon.objects.count() == 2
    assert wagon.weighings.count() == 2


def test_reclassifies_only_the_wrong_entry_without_duplicate_exit(case):
    user, _, _, item = case
    wagon = Wagon.objects.create(direction="passage", workflow="simple", number="123ABC13", status=st.AT_SILO, cargo_name="Отруби", gross_weight_kg=item.weight_kg)
    wrong = WeighingRecord.objects.create(wagon=wagon, kind="gross", source="scale", weight_kg=item.weight_kg, photo=item.photo, photo_request_id=item.photo_request_id, orientation="rear")
    item.status, item.action, item.wagon = "assigned", "entry", wagon
    item.save()
    result = complete(case)
    wrong.refresh_from_db()
    assert result.wagon.pk == wagon.pk and wrong.kind == "tare"
    assert result.wagon.net_weight_kg == 5200 and result.action == "exit"
    assert wagon.weighings.count() == 2


@pytest.mark.parametrize("change", ["plate", "future", "manual", "rear", "photo", "too_heavy", "discarded"])
def test_rejects_unsafe_tare_sources_atomically(case, change):
    _, trip, source, item = case
    if change == "plate": trip.number = "999ABC13"; trip.save()
    if change == "future": WeighingRecord.objects.filter(pk=source.pk).update(created_at=timezone.now())
    if change == "manual": source.source="manual"; source.save()
    if change == "rear": source.orientation="rear"; source.save()
    if change == "photo": source.photo=""; source.save()
    if change == "too_heavy": source.weight_kg=9300; source.save()
    if change == "discarded": item.status="discarded"; item.save()
    with pytest.raises(ValidationError): complete(case)
    assert Wagon.objects.count() == 1
    assert WeighingRecord.objects.count() == 1


def test_rear_is_not_a_new_entry(case):
    user, _, _, item = case
    with pytest.raises(ValidationError):
        services.create_passage_from_unassigned_weighing(item, user)
    assert Wagon.objects.count() == 1


def test_api_permissions_and_candidates(case, auth_client, user_with_perms):
    user, _, source, item = case
    url=f"/api/grain/unassigned-weighings/{item.pk}/"
    assert auth_client(user_with_perms("tare-viewer",codes=["grain.view"])).post(url+"historical-exit/",{},format="json").status_code == 403
    response=auth_client(user).get(url+"tare-candidates/?number=123ABC13")
    assert response.status_code == 200 and response.data[0]["id"] == source.pk
    response=auth_client(user).post(url+"historical-exit/", {"number":"123ABC13","reference_record":source.pk,"reason":"Подтверждено по фото"},format="json")
    assert response.status_code == 200 and response.data["action"] == "exit"
