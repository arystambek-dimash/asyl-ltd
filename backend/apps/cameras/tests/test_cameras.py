import pytest
from django.core.signing import TimestampSigner

from apps.cameras.views import CAM_COOKIE

pytestmark = pytest.mark.django_db


def test_camera_list_for_staff(auth_client, operator):
    resp = auth_client(operator).get("/api/cameras/")
    assert resp.status_code == 200
    assert len(resp.data) == 8
    assert resp.data[0]["src"] == "cam1"
    assert "zone" in resp.data[0]


def test_camera_list_denied_for_portal_client(auth_client, client_user):
    resp = auth_client(client_user).get("/api/cameras/")
    assert resp.status_code == 403


def test_camera_list_denied_anonymous(api_client):
    resp = api_client.get("/api/cameras/")
    assert resp.status_code == 401


def test_token_sets_cookie(auth_client, operator):
    resp = auth_client(operator).post("/api/cameras/token/")
    assert resp.status_code == 204
    cookie = resp.cookies.get(CAM_COOKIE)
    assert cookie is not None
    assert cookie["httponly"]


def test_token_denied_for_portal_client(auth_client, client_user):
    resp = auth_client(client_user).post("/api/cameras/token/")
    assert resp.status_code == 403


def test_auth_accepts_valid_cookie(api_client, operator):
    api_client.cookies[CAM_COOKIE] = TimestampSigner(salt="cameras").sign(str(operator.pk))
    resp = api_client.get("/api/cameras/auth/")
    assert resp.status_code == 204


def test_auth_rejects_missing_or_bad_cookie(api_client):
    assert api_client.get("/api/cameras/auth/").status_code == 403
    api_client.cookies[CAM_COOKIE] = "garbage"
    assert api_client.get("/api/cameras/auth/").status_code == 403
