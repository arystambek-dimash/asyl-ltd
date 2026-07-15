import pytest
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def _product(name="Премиум", active=True):
    return Product.objects.create(
        name=name, color="Red", weight_kg="50", price="100.00", is_active=active)


def test_list_hides_archived(auth_client, manager):
    _product(name="Активный")
    _product(name="Архивный", active=False)
    resp = auth_client(manager).get("/api/products/")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.data}
    assert "Активный" in names
    assert "Архивный" not in names


def test_list_archived_param(auth_client, manager):
    _product(name="Активный")
    _product(name="Архивный", active=False)
    resp = auth_client(manager).get("/api/products/?archived=1")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.data}
    assert names == {"Архивный"}


def test_archive_action(auth_client, manager):
    p = _product()
    resp = auth_client(manager).post(f"/api/products/{p.id}/archive/")
    assert resp.status_code == 200
    p.refresh_from_db()
    assert p.is_active is False


def test_restore_action(auth_client, manager):
    p = _product(active=False)
    resp = auth_client(manager).post(f"/api/products/{p.id}/restore/")
    assert resp.status_code == 200
    p.refresh_from_db()
    assert p.is_active is True


def test_delete_archives_not_hard_delete(auth_client, manager):
    p = _product()
    resp = auth_client(manager).delete(f"/api/products/{p.id}/")
    assert resp.status_code == 204
    p.refresh_from_db()
    assert p.is_active is False


def test_operator_cannot_archive(auth_client, operator):
    p = _product()
    resp = auth_client(operator).post(f"/api/products/{p.id}/archive/")
    assert resp.status_code == 403
