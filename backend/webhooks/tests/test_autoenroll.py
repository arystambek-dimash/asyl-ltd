import pytest
from django.test import override_settings
from rest_framework.test import APIClient
from webhooks.models import Camera

pytestmark = pytest.mark.django_db


@override_settings(CAMERA_ENROLL_KEY="enroll-xyz")
def test_unknown_camera_with_enroll_key_creates_pending():
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-09", "plate": "X"},
               format="json", HTTP_X_CAMERA_KEY="enroll-xyz")
    assert r.status_code == 200
    assert r.data["status"] == "pending"
    cam = Camera.objects.get(camera_id="gate-09")
    assert cam.status == "pending" and cam.is_active is False


@override_settings(CAMERA_ENROLL_KEY="enroll-xyz")
def test_unknown_without_enroll_key_404():
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-09", "plate": "X"},
               format="json", HTTP_X_CAMERA_KEY="wrong")
    assert r.status_code == 404


def test_pending_camera_not_processed():
    Camera.objects.create(camera_id="gate-09", status="pending", is_active=False, api_key="k")
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-09", "plate": "X"},
               format="json", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 200 and r.data["status"] == "pending"


def test_bind_activates_camera(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.save()
    cam = Camera.objects.create(camera_id="gate-09", status="pending", is_active=False, api_key="old")
    r = auth_client(u).post(f"/api/cameras/{cam.id}/bind/",
                            {"kind": "entry", "name": "Ворота"}, format="json")
    assert r.status_code == 200
    cam.refresh_from_db()
    assert cam.status == "active" and cam.is_active and cam.kind == "entry"
    assert cam.api_key != "old"
    assert len(r.data["api_key"]) >= 24  # full key revealed
