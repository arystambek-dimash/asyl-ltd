import pytest
from rest_framework.test import APIClient
from webhooks.models import Camera

pytestmark = pytest.mark.django_db


def _cam(**kw):
    d = dict(name="g", camera_id="gate-01", kind="entry", api_key="secret123",
             response_template="", is_active=True)
    d.update(kw)
    return Camera.objects.create(**d)


def test_unknown_camera_404():
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "nope", "plate": "X"}, format="json")
    assert r.status_code == 404


def test_bad_key_401():
    _cam()
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-01", "plate": "X"},
               format="json", HTTP_X_CAMERA_KEY="wrong")
    assert r.status_code == 401


def test_inactive_403():
    _cam(is_active=False)
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-01", "plate": "X"},
               format="json", HTTP_X_CAMERA_KEY="secret123")
    assert r.status_code == 403


def test_valid_call_200_deny_when_no_order():
    _cam()
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-01", "plate": "999ZZ99"},
               format="json", HTTP_X_CAMERA_KEY="secret123")
    assert r.status_code == 200
    assert r.data["decision"] == "deny"
