import pytest

pytestmark = pytest.mark.django_db


def test_warehouse_can_be_created_with_only_a_unique_display_name(
    auth_client,
    boss,
):
    api = auth_client(boss)

    created = api.post(
        "/api/warehouses/",
        {"name": "  Мельница 2  "},
        format="json",
    )
    duplicate = api.post(
        "/api/warehouses/",
        {"name": "мЕЛЬНИЦА 2"},
        format="json",
    )

    assert created.status_code == 201
    assert created.data["name"] == "Мельница 2"
    assert created.data["code"].startswith("wh-")
    assert created.data["address"] == ""
    assert duplicate.status_code == 400
    assert duplicate.data["detail"]["name"] == [
        "Склад с таким названием уже существует"
    ]
