import pytest
from rest_framework.test import APIClient

from apps.clients.models import Client, Store
from apps.clients.views import StoreViewSet

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_create_store_via_api(manager):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    r = _api(manager).post("/api/stores/", {
        "client": client.id, "name": "Магазин №1",
        "payment_schedule_type": "monthly", "payment_days": [5, 20],
    }, format="json")
    assert r.status_code == 201
    assert r.data["name"] == "Магазин №1"
    assert r.data["client_name"] == client.name
    assert r.data["payment_days"] == [5, 20]


def test_client_picker_exposes_only_form_reference_fields(manager):
    client = Client.objects.create_with_user(
        first_name="A",
        last_name="B",
        phone="secret",
        iin="123456789012",
        bank_account="KZSECRET",
    )

    response = _api(manager).get("/api/clients/picker/")

    assert response.status_code == 200
    assert response.data == [{"id": client.id, "name": client.name}]


def test_can_create_store_for_any_client(manager):
    foreign = Client.objects.create_with_user(
        first_name="Field", last_name="Client", phone="x")

    response = _api(manager).post("/api/stores/", {
        "client": foreign.id, "name": "Чужой магазин",
    }, format="json")

    assert response.status_code == 201


def test_stale_store_patch_does_not_restore_previous_client(manager, monkeypatch):
    first = Client.objects.create_with_user(
        first_name="First", phone="store-stale-first",
    )
    second = Client.objects.create_with_user(
        first_name="Second", phone="store-stale-second",
    )
    store = Store.objects.create(client=first, name="До изменения")
    stale_store = Store.objects.select_related("client").get(pk=store.pk)
    Store.objects.filter(pk=store.pk).update(client=second)
    monkeypatch.setattr(StoreViewSet, "get_object", lambda _view: stale_store)

    response = _api(manager).patch(
        f"/api/stores/{store.pk}/",
        {"name": "После изменения"},
        format="json",
    )

    assert response.status_code == 200
    store.refresh_from_db()
    assert store.name == "После изменения"
    assert store.client_id == second.pk


def test_existing_store_client_is_immutable(manager):
    first = Client.objects.create_with_user(
        first_name="Owner", phone="store-owner-first",
    )
    second = Client.objects.create_with_user(
        first_name="Other", phone="store-owner-second",
    )
    store = Store.objects.create(client=first, name="Закреплённый магазин")

    response = _api(manager).patch(
        f"/api/stores/{store.pk}/",
        {"client": second.pk},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "client_locked"
    store.refresh_from_db()
    assert store.client_id == first.pk
