import pytest

from apps.clients.models import Client, Store
from apps.clients.views import StoreViewSet

pytestmark = pytest.mark.django_db


def test_create_store_via_api(manager, api_as):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    r = api_as(manager).post("/api/stores/", {
        "client": client.id, "name": "Магазин №1",
        "payment_schedule_type": "monthly", "payment_days": [5, 20],
    }, format="json")
    assert r.status_code == 201
    assert r.data["name"] == "Магазин №1"
    assert r.data["client_name"] == client.name
    assert r.data["payment_days"] == [5, 20]


def test_client_picker_exposes_only_form_reference_fields(manager, api_as):
    client = Client.objects.create_with_user(
        first_name="A",
        last_name="B",
        phone="secret",
        iin="123456789012",
        bank_account="KZSECRET",
    )

    response = api_as(manager).get("/api/clients/picker/")

    assert response.status_code == 200
    assert response.data == [{"id": client.id, "name": client.name}]


def test_stale_store_patch_does_not_restore_previous_client(manager, monkeypatch, api_as):
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

    response = api_as(manager).patch(
        f"/api/stores/{store.pk}/",
        {"name": "После изменения"},
        format="json",
    )

    assert response.status_code == 200
    store.refresh_from_db()
    assert store.name == "После изменения"
    assert store.client_id == second.pk


def test_existing_store_client_is_immutable(manager, api_as):
    first = Client.objects.create_with_user(
        first_name="Owner", phone="store-owner-first",
    )
    second = Client.objects.create_with_user(
        first_name="Other", phone="store-owner-second",
    )
    store = Store.objects.create(client=first, name="Закреплённый магазин")

    response = api_as(manager).patch(
        f"/api/stores/{store.pk}/",
        {"client": second.pk},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "client_locked"
    store.refresh_from_db()
    assert store.client_id == first.pk


@pytest.mark.parametrize(
    "payload",
    [
        {"payment_schedule_type": "yearly", "payment_days": [5]},
        {"payment_schedule_type": "monthly", "payment_days": ["5"]},
        {"payment_schedule_type": "monthly", "payment_days": [0]},
        {"payment_schedule_type": "monthly", "payment_days": [32]},
        {"payment_schedule_type": "monthly", "payment_days": []},
        {"payment_schedule_type": "monthly", "payment_days": [True]},
        {"payment_schedule_type": "weekly", "payment_days": [8]},
        {"payment_schedule_type": "weekly", "payment_days": 1},
    ],
)
def test_store_api_rejects_broken_payment_schedule(manager, payload, api_as):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")

    r = api_as(manager).post("/api/stores/", {
        "client": client.id, "name": "Магазин", **payload,
    }, format="json")

    assert r.status_code == 400
    assert not Store.objects.exists()


def test_store_api_normalizes_payment_days(manager, api_as):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    api = api_as(manager)
    created = api.post("/api/stores/", {
        "client": client.id, "name": "Магазин",
        "payment_schedule_type": "weekly", "payment_days": [5, 1, 5],
    }, format="json")
    assert created.status_code == 201
    assert created.data["payment_days"] == [1, 5]

    # Смена типа без дней проверяет прежние дни под новый тип: 5 и 1 годятся и
    # для месяца, а при «без расписания» дни сбрасываются.
    monthly = api.patch(f"/api/stores/{created.data['id']}/", {
        "payment_schedule_type": "monthly",
    }, format="json")
    assert monthly.status_code == 200
    assert monthly.data["payment_days"] == [1, 5]
    cleared = api.patch(f"/api/stores/{created.data['id']}/", {
        "payment_schedule_type": "none",
    }, format="json")
    assert cleared.status_code == 200
    assert cleared.data["payment_days"] == []

    # Правка без графика не спотыкается о старые данные.
    Store.objects.filter(pk=created.data["id"]).update(
        payment_schedule_type="monthly", payment_days=["5"])
    renamed = api.patch(f"/api/stores/{created.data['id']}/", {
        "name": "Новое имя",
    }, format="json")
    assert renamed.status_code == 200


def test_store_days_patch_on_legacy_schedule_type_is_400_not_500(manager, api_as):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    store = Store.objects.create(client=client, name="S", payment_schedule_type="yearly")

    r = api_as(manager).patch(f"/api/stores/{store.id}/", {"payment_days": [5]}, format="json")

    assert r.status_code == 400
    assert "payment_schedule_type" in r.data["detail"]
