import pytest
import fakeredis
from rest_framework.test import APIClient
from webhooks import frame_store
from webhooks.models import Camera, VideoJob

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(frame_store, "_client", fakeredis.FakeRedis())


def _cam(key="k1"):
    return Camera.objects.create(name="c", camera_id="counter-01", kind="counter",
                                 status="active", api_key=key, is_active=True)


def _job(cam, status="processing"):
    from clients.models import Client
    from orders.models import Order
    cl = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=cl, status="loading", truck_number="X1")
    return VideoJob.objects.create(order=o, camera=cam, status=status)


def test_frame_post_stores_jpeg():
    cam = _cam(); job = _job(cam)
    r = APIClient().post(f"/api/video-jobs/{job.id}/frame/", b"\xff\xd8frame",
                         content_type="application/octet-stream",
                         HTTP_X_CAMERA_KEY="k1")
    assert r.status_code == 204
    assert frame_store.get(job.id) == b"\xff\xd8frame"


def test_frame_post_bad_key_401():
    cam = _cam(); job = _job(cam)
    r = APIClient().post(f"/api/video-jobs/{job.id}/frame/", b"x",
                         content_type="application/octet-stream",
                         HTTP_X_CAMERA_KEY="wrong")
    assert r.status_code == 401


def test_stream_content_type_and_terminates_when_done():
    cam = _cam(key="k2")
    # job NOT processing -> generator must terminate immediately (no hang)
    job = _job(cam, status="done")
    r = APIClient().get(f"/api/video-jobs/{job.id}/stream/")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("multipart/x-mixed-replace")
    body = b"".join(r.streaming_content)  # must not block; job is done
    assert body == b"" or b"--frame" in body
