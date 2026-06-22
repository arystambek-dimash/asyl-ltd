import pytest
from webhooks.models import Camera

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.save()
    return auth_client(u)


def test_create_camera_returns_full_key(admin_client):
    r = admin_client.post("/api/cameras/", {
        "name": "Ворота", "camera_id": "gate-01", "kind": "entry",
        "response_template": "",
    }, format="json")
    assert r.status_code == 201
    assert len(r.data["api_key"]) >= 24
    assert Camera.objects.get(camera_id="gate-01").kind == "entry"


def test_list_masks_key(admin_client):
    admin_client.post("/api/cameras/", {"name": "g", "camera_id": "gate-01", "kind": "entry"}, format="json")
    r = admin_client.get("/api/cameras/")
    assert r.status_code == 200
    assert r.data[0]["api_key"].startswith("•")


def test_regenerate_key(admin_client):
    admin_client.post("/api/cameras/", {"name": "g", "camera_id": "gate-01", "kind": "entry"}, format="json")
    cam = Camera.objects.get()
    old = cam.api_key
    r = admin_client.post(f"/api/cameras/{cam.id}/regenerate_key/")
    assert r.status_code == 200 and r.data["api_key"] != old


def test_simulate_does_not_mutate_order(admin_client, boss):
    from clients.models import Client
    from orders.models import Order
    c = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=c, status="paid", truck_number="123ABC02")
    admin_client.post("/api/cameras/", {"name": "g", "camera_id": "gate-01", "kind": "entry"}, format="json")
    cam = Camera.objects.get()
    r = admin_client.post(f"/api/cameras/{cam.id}/simulate/",
                          {"plate": "123ABC02"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "paid"


def test_non_manager_cannot_create(auth_client, make_user):
    u = make_user(username="plain")
    r = auth_client(u).post("/api/cameras/", {"name": "g", "camera_id": "g1", "kind": "entry"}, format="json")
    assert r.status_code == 403
