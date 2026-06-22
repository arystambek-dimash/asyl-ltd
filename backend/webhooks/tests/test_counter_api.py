import pytest
import fakeredis
from rest_framework.test import APIClient
from webhooks.models import Camera
from webhooks import counter_store

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(counter_store, "_client", fakeredis.FakeRedis())


def _counter_cam():
    return Camera.objects.create(name="cnt", camera_id="counter-01", kind="counter",
                                 status="active", api_key="k", is_active=True)


def test_increment_grows_redis_not_order():
    cam = _counter_cam()
    c = APIClient()
    for _ in range(3):
        r = c.post("/api/webhook/camera/",
                   {"camera_id": "counter-01", "increment": 1},
                   format="json", HTTP_X_CAMERA_KEY="k")
        assert r.status_code == 200
    assert counter_store.get(cam.pk) == 3


def test_get_count(auth_client, make_user):
    u = make_user(username="v")
    from rbac.models import Permission, Role
    from employees.models import Employee
    role = Role.objects.create(name="r")
    p, _ = Permission.objects.get_or_create(
        code="cameras.view", defaults={"section": "cameras", "action": "view", "label": "x"})
    role.permissions.add(p)
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    cam = _counter_cam()
    counter_store.increment(cam.pk, by=12)
    r = auth_client(u).get(f"/api/count/{cam.pk}/")
    assert r.status_code == 200 and r.data["bags"] == 12
