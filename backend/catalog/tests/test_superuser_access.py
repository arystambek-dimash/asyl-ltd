import pytest
from catalog.models import Grade

pytestmark = pytest.mark.django_db


def test_superuser_can_create_grade(auth_client, make_user):
    admin = make_user(username="admin")
    admin.is_superuser = True
    admin.is_staff = True
    admin.save()
    resp = auth_client(admin).post("/api/grades/", {"name": "Премиум"})
    assert resp.status_code == 201
    assert Grade.objects.filter(name="Премиум").exists()
