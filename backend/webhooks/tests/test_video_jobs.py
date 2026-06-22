import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from webhooks.models import Camera, VideoJob

pytestmark = pytest.mark.django_db


def _counter_cam():
    return Camera.objects.create(name="cnt", camera_id="counter-01", kind="counter",
                                 status="active", api_key="k", is_active=True)


def _loading_order(boss):
    from catalog.models import Grade, Packaging, Product
    from clients.models import Client
    from orders.models import Order, OrderItem, Payment
    from warehouse.services import receive_stock
    from shipments.services import record_arrival
    from decimal import Decimal
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, 100, boss)
    cl = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=cl, status="paid", truck_number="123ABC02")
    OrderItem.objects.create(order=o, product=prod, quantity=50)
    Payment.objects.create(order=o, amount=o.total_amount)
    record_arrival(o, "123ABC02", Decimal("0"), boss)
    return o


def _mp4():
    return SimpleUploadedFile("test.mp4", b"\x00\x00fake", content_type="video/mp4")


def test_upload_creates_queued_job(auth_client, boss):
    _counter_cam()
    o = _loading_order(boss)
    r = auth_client(boss).post(f"/api/orders/{o.id}/upload-video/",
                               {"video": _mp4()}, format="multipart")
    assert r.status_code == 201
    job = VideoJob.objects.get()
    assert job.status == "queued" and job.order_id == o.id


def test_upload_bad_extension_400(auth_client, boss):
    _counter_cam()
    o = _loading_order(boss)
    bad = SimpleUploadedFile("x.txt", b"hi", content_type="text/plain")
    r = auth_client(boss).post(f"/api/orders/{o.id}/upload-video/",
                               {"video": bad}, format="multipart")
    assert r.status_code == 400


def test_upload_no_permission_403(auth_client, make_user):
    u = make_user(username="plain")
    from clients.models import Client
    from orders.models import Order
    _counter_cam()
    cl = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=cl, status="loading", truck_number="X")
    bad = SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4")
    r = auth_client(u).post(f"/api/orders/{o.id}/upload-video/",
                            {"video": bad}, format="multipart")
    assert r.status_code == 403


import fakeredis
from rest_framework.test import APIClient
from webhooks import counter_store


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(counter_store, "_client", fakeredis.FakeRedis())


def _queued_job(boss):
    cam = _counter_cam()
    o = _loading_order(boss)
    return VideoJob.objects.create(order=o, camera=cam, video=_mp4(), status="queued"), cam, o


def test_next_claims_job(boss):
    job, cam, o = _queued_job(boss)
    c = APIClient()
    r = c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 200
    assert r.data["id"] == job.id and "video_url" in r.data
    job.refresh_from_db()
    assert job.status == "processing"


def test_next_empty_204():
    _counter_cam()
    c = APIClient()
    r = c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 204


def test_next_bad_key_401():
    _counter_cam()
    c = APIClient()
    r = c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="nope")
    assert r.status_code == 401


def test_complete_records_loading(boss):
    job, cam, o = _queued_job(boss)
    counter_store.increment(cam.pk, by=40)
    c = APIClient()
    c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="k")
    r = c.post(f"/api/video-jobs/{job.id}/complete/", {"bags": 40},
               format="json", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 200
    job.refresh_from_db(); o.refresh_from_db()
    assert job.status == "done" and job.bags_counted == 40
    assert o.status == "loading" and o.shipment.bags_loaded == 40
    assert counter_store.get(cam.pk) == 0


def test_fail_sets_failed(boss):
    job, cam, o = _queued_job(boss)
    c = APIClient()
    r = c.post(f"/api/video-jobs/{job.id}/fail/", {"error": "boom"},
               format="json", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 200
    job.refresh_from_db()
    assert job.status == "failed" and job.error == "boom"


def test_list_by_order(auth_client, boss):
    job, cam, o = _queued_job(boss)
    r = auth_client(boss).get(f"/api/video-jobs/?order={o.id}")
    assert r.status_code == 200 and len(r.data) == 1
