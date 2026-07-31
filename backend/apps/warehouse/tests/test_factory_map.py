import pytest

from apps.warehouse.models import FactoryMap

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="root-map", password="pass12345", email="root@example.com"
    )


def test_factory_staff_can_view_map(auth_client, operator):
    response = auth_client(operator).get("/api/factory/map/")

    assert response.status_code == 200
    assert response.data["title"] == "Схема завода"
    assert len(response.data["zones"]) >= 1


def test_grain_staff_can_view_common_factory_map(auth_client, user_with_perms):
    grain_user = user_with_perms("grain-map", codes=["grain.view"])

    response = auth_client(grain_user).get("/api/factory/map/")

    assert response.status_code == 200


def test_regular_staff_cannot_edit_map(auth_client, operator):
    response = auth_client(operator).put(
        "/api/factory/map/", {"title": "Новая схема", "zones": []}, format="json"
    )

    assert response.status_code == 403


def test_superuser_can_replace_factory_map(auth_client, superuser):
    payload = {
        "title": "Производственная площадка",
        "zones": [
            {
                "id": "gate-2",
                "name": "Северная проходная",
                "kind": "gate",
                "x": 20,
                "y": 30,
                "width": 180,
                "height": 90,
                "color": "#C58A35",
                "note": "Основной въезд",
            }
        ],
    }

    response = auth_client(superuser).put("/api/factory/map/", payload, format="json")

    assert response.status_code == 200
    assert response.data["title"] == payload["title"]
    assert response.data["zones"] == payload["zones"]
    row = FactoryMap.objects.get(singleton=True)
    assert row.updated_by == superuser


def test_factory_map_rejects_zone_outside_canvas(auth_client, superuser):
    response = auth_client(superuser).put(
        "/api/factory/map/",
        {
            "title": "Схема",
            "zones": [
                {
                    "id": "outside",
                    "name": "За границей",
                    "kind": "utility",
                    "x": 1190,
                    "y": 10,
                    "width": 100,
                    "height": 100,
                    "color": "#112233",
                    "note": "",
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 400
