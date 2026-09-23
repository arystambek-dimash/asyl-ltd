"""Коды товара в отчётах о вагонах на странице «Товары» (catalog.edit)."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product, ProductAlias
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db


def _product(name="Мука высший сорт"):
    return Product.objects.create(name=name, color="Red", weight_kg="50")


def _url(product, alias=None):
    return f"/api/products/{product.pk}/aliases/" + (f"{alias.pk}/" if alias else "")


def test_manager_adds_a_report_code_to_a_product(auth_client, manager):
    product = _product()

    response = auth_client(manager).post(_url(product), {"code": " д1с "}, format="json")

    assert response.status_code == 200, response.data
    alias = ProductAlias.objects.get()
    assert (alias.code, alias.product, alias.created_by) == ("Д1C", product, manager)
    # Людям — как ввели, сравнение — по ключу.
    assert response.data["aliases"] == [{"id": alias.pk, "code": "д1с"}]
    assert EventLog.objects.filter(event_type="catalog", payload__code="Д1C", payload__product_id=product.pk).exists()


def test_code_of_another_product_moves_only_on_request(auth_client, manager):
    old, new = _product("Мука первый сорт"), _product()
    ProductAlias.objects.create(code="Д1с", product=old)
    api = auth_client(manager)

    refused = api.post(_url(new), {"code": "Д1c"}, format="json")
    assert refused.status_code == 400
    assert refused.data["code"] == "alias_taken"
    assert "Мука первый сорт" in refused.data["detail"]
    assert ProductAlias.objects.get().product == old

    moved = api.post(_url(new), {"code": "Д1c", "move": True}, format="json")
    assert moved.status_code == 200, moved.data
    assert ProductAlias.objects.get().product == new


def test_code_of_an_archived_product_moves_without_asking(auth_client, manager):
    old, new = _product("Мука первый сорт"), _product()
    ProductAlias.objects.create(code="Д1с", product=old)
    Product.objects.filter(pk=old.pk).update(is_active=False)

    response = auth_client(manager).post(_url(new), {"code": "Д1с"}, format="json")

    assert response.status_code == 200, response.data
    assert ProductAlias.objects.get().product == new


def test_same_code_again_is_idempotent(auth_client, manager):
    product = _product()
    api = auth_client(manager)
    api.post(_url(product), {"code": "Д1с"}, format="json")

    assert api.post(_url(product), {"code": "Д1C"}, format="json").status_code == 200
    assert ProductAlias.objects.count() == 1


def test_manager_removes_a_code(auth_client, manager):
    product = _product()
    alias = ProductAlias.objects.create(code="Д1с", product=product)

    response = auth_client(manager).delete(_url(product, alias))

    assert response.status_code == 200, response.data
    assert response.data["aliases"] == []
    assert not ProductAlias.objects.exists()


def test_code_of_another_product_cannot_be_removed_through_this_one(auth_client, manager):
    product, other = _product(), _product("Другой")
    alias = ProductAlias.objects.create(code="Д1с", product=other)

    assert auth_client(manager).delete(_url(product, alias)).status_code == 404
    assert ProductAlias.objects.exists()


def test_empty_code_is_an_input_error(auth_client, manager):
    response = auth_client(manager).post(_url(_product()), {"code": " — "}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "alias_empty"


def test_codes_need_catalog_edit(auth_client, user_with_perms):
    viewer = user_with_perms("catalog-viewer", codes=["catalog.view"])
    product = _product()
    alias = ProductAlias.objects.create(code="Д1с", product=product)
    api = auth_client(viewer)

    assert api.post(_url(product), {"code": "Б2"}, format="json").status_code == 403
    assert api.delete(_url(product, alias)).status_code == 403
    assert api.get(f"/api/products/{product.pk}/").data["aliases"] == [{"id": alias.pk, "code": "Д1с"}]


def test_product_list_loads_codes_without_n_plus_one(auth_client, manager, django_assert_max_num_queries):
    api = auth_client(manager)
    ProductAlias.objects.create(code="А1", product=_product("А"))
    with CaptureQueriesContext(connection) as small:
        api.get("/api/products/")
    for index in range(4):
        product = _product(f"Товар {index}")
        ProductAlias.objects.create(code=f"Т{index}", product=product)
        ProductAlias.objects.create(code=f"Т{index}X", product=product)

    with django_assert_max_num_queries(len(small.captured_queries)):
        rows = api.get("/api/products/").data

    assert sum(len(row["aliases"]) for row in rows) == 9
