from decimal import Decimal
from uuid import uuid4

import pytest

from apps.catalog.models import ClientPrice
from apps.employees.models import Employee, EmployeeShop, ShopSale, ShopStock
from apps.eventlog.models import EventLog
from apps.orders.models import Order
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem, Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api(auth_client, admin_user):
    return auth_client(admin_user)


@pytest.fixture
def flour(boss, make_product):
    """«Д1с» со 100 мешками на основном складе."""
    from apps.warehouse.services import receive_stock

    product = make_product(name="Д1с")
    receive_stock(product, 100, boss)
    return product


@pytest.fixture
def worker(user_with_perms, departments):
    """Оператор, которому разрешено создавать заказы."""
    return user_with_perms("worker", codes=["orders.create"], department=departments[0])


@pytest.fixture
def shop(worker, admin_user):
    from apps.employees.shop import set_employee_shop

    return set_employee_shop(worker.employee, admin_user, enabled=True)


def _price(shop, product, price="5000"):
    return ClientPrice.objects.create(client=shop.client, product=product, price=Decimal(price))


def _bags(product, warehouse=None):
    warehouse = warehouse or Warehouse.objects.get(is_default=True)
    return StockItem.objects.get(product=product, warehouse=warehouse).bags


def _order(api, product, quantity=10, *, key=None, **extra):
    return api.post("/api/shop-orders/", {
        "idempotency_key": str(key or uuid4()),
        "items": [{"product": product.pk, "quantity": quantity}],
        **extra,
    }, format="json")


@pytest.fixture
def stocked_shop(shop, flour):
    _price(shop, flour)
    ShopStock.objects.create(shop=shop, product=flour, bags=40)
    return shop


def _shop_bags(shop, product):
    return ShopStock.objects.get(shop=shop, product=product).bags


def test_admin_turns_shop_on_when_creating_employee(admin_api, departments):
    response = admin_api.post(
        "/api/employees/",
        {
            "username": "ivan",
            "password": "A-safe-pass-2026!",
            "first_name": "Иван",
            "last_name": "Петров",
            "sales_department": departments[1].pk,
            "shop": {"enabled": True},
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    shop = EmployeeShop.objects.get(employee__user__username="ivan")
    assert response.data["shop"] == {
        "enabled": True,
        "client": shop.client_id,
        "name": "Магазин Иван Петров",
        "warehouse": Warehouse.objects.get(is_default=True).pk,
        "warehouse_name": shop.warehouse.name,
    }
    # Магазин — покупатель в отделе сотрудника, без входа в портал.
    assert shop.client.department == departments[1]
    assert not shop.client.user.is_active


def test_shop_is_a_security_setting(auth_client, admin_api, worker, get_permission):
    manager = Employee.objects.create(user=worker.__class__.objects.create_user(username="hr"), phone="")
    manager.permissions.add(get_permission("employees.view"), get_permission("employees.manage"))
    employee = worker.employee

    profile = auth_client(manager.user).patch(
        f"/api/employees/{employee.pk}/", {"shop": {"enabled": True}}, format="json",
    )
    assert profile.status_code == 400
    assert not EmployeeShop.objects.exists()

    security = admin_api.patch(
        f"/api/employees/{employee.pk}/security/", {"shop": {"enabled": True, "name": "Точка у рынка"}}, format="json",
    )
    assert security.status_code == 200, security.data
    assert security.data["shop"]["name"] == "Точка у рынка"
    assert EventLog.objects.filter(
        event_type="employee_security", payload__after__shop__name="Точка у рынка",
    ).exists()


def test_turning_shop_off_keeps_client_and_on_again_reuses_it(admin_api, auth_client, worker, shop):
    url = f"/api/employees/{worker.employee.pk}/security/"
    assert auth_client(worker).get("/api/auth/me/").data["shop"] is not None

    off = admin_api.patch(url, {"shop": {"enabled": False}}, format="json")
    assert off.data["shop"]["enabled"] is False
    # Пункт «Заказы магазина» пропадает у самого сотрудника.
    assert auth_client(worker).get("/api/auth/me/").data["shop"] is None
    assert not EmployeeShop.objects.get(pk=shop.pk).is_active

    on = admin_api.patch(url, {"shop": {"enabled": True}}, format="json")
    assert on.data["shop"] == {**off.data["shop"], "enabled": True}
    assert EmployeeShop.objects.count() == 1


def test_shop_follows_employee_department(admin_api, worker, shop, departments):
    response = admin_api.patch(
        f"/api/employees/{worker.employee.pk}/security/", {"sales_department": departments[1].pk}, format="json",
    )

    assert response.status_code == 200, response.data
    shop.client.refresh_from_db()
    assert shop.client.department == departments[1]


def test_shop_stays_while_its_truck_is_loading(admin_api, worker, shop, departments):
    """Перенос магазина — тот же, что у клиента: во время погрузки отдел не меняется."""
    Order.objects.create(client=shop.client, status="loading", loading_camera="cam2")

    response = admin_api.patch(
        f"/api/employees/{worker.employee.pk}/security/", {"sales_department": departments[1].pk}, format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "active_loading"
    shop.client.refresh_from_db()
    worker.employee.refresh_from_db()
    assert shop.client.department == departments[0]
    assert worker.employee.sales_department == departments[0]


def test_menu_and_api_require_active_shop_and_order_permission(auth_client, worker, shop, user_with_perms, get_permission):
    api = auth_client(worker)
    assert api.get("/api/auth/me/").data["shop"] == {
        "name": shop.client.name, "warehouse_name": shop.warehouse.name,
    }
    worker.employee.permissions.remove(get_permission("orders.create"))
    worker.__dict__.pop("perm_codes", None)
    assert api.get("/api/auth/me/").data["shop"] is None
    assert api.get("/api/shop-orders/").status_code == 403
    assert api.get("/api/shop-orders/catalog/").status_code == 403
    without_shop = user_with_perms("no-shop", codes=["orders.create"])
    assert auth_client(without_shop).get("/api/shop-orders/").status_code == 404


def test_old_my_shop_api_removed(auth_client, worker, shop):
    api = auth_client(worker)
    for url in ["/api/my-shop/", "/api/my-shop/catalog/", "/api/my-shop/orders/"]:
        assert api.get(url).status_code == 404


def test_catalog_shows_separate_stock_and_shop_prices(auth_client, worker, stocked_shop, flour, make_product):
    unpriced = make_product(name="Без цены")
    archived = make_product(name="Архив", is_active=False)
    rows = {row["id"]: row for row in auth_client(worker).get("/api/shop-orders/catalog/").data}
    assert rows[flour.pk]["price"] == "5000.00"
    assert rows[flour.pk]["stock_bags"] == 40
    assert rows[unpriced.pk]["price"] is None
    assert rows[unpriced.pk]["stock_bags"] == 0
    assert archived.pk not in rows


def test_sale_only_deducts_shop_and_keeps_separate_history(auth_client, worker, stocked_shop, flour):
    api = auth_client(worker)
    response = _order(api, flour, quantity=30)
    assert response.status_code == 201, response.data
    sale = ShopSale.objects.get(pk=response.data["id"])
    assert sale.status == "completed"
    assert sale.client is None
    assert sale.created_by == worker
    assert _shop_bags(stocked_shop, flour) == 10
    assert _bags(flour) == 100
    assert not Order.objects.exists()
    assert not Shipment.objects.exists()
    assert response.data["total_amount"] == "150000.00"
    assert response.data["bags"] == 30
    assert response.data["client_name"] is None
    assert [row["id"] for row in api.get("/api/shop-orders/").data] == [sale.pk]
    summary = api.get("/api/shop-orders/summary/").data
    assert summary == {
        "name": stocked_shop.client.name, "currency": "KZT",
        "client": stocked_shop.client_id, "warehouse_name": stocked_shop.warehouse.name,
    }


def test_sale_repeat_is_idempotent_and_changed_payload_rejected(auth_client, worker, stocked_shop, flour):
    api = auth_client(worker)
    key = uuid4()
    first = _order(api, flour, key=key)
    second = _order(api, flour, key=key)
    assert first.status_code == second.status_code == 201
    assert first.data == second.data
    assert ShopSale.objects.count() == 1
    assert _shop_bags(stocked_shop, flour) == 30
    assert _order(api, flour, quantity=11, key=key).status_code == 400
    assert _shop_bags(stocked_shop, flour) == 30


def test_cancel_and_repeat_create_return_stock_once(auth_client, worker, stocked_shop, flour):
    api = auth_client(worker)
    key = uuid4()
    sale_id = _order(api, flour, key=key).data["id"]
    url = f"/api/shop-orders/{sale_id}/cancel/"
    first = api.post(url)
    second = api.post(url)
    assert first.status_code == second.status_code == 200
    assert first.data == second.data
    assert first.data["status"] == "cancelled"
    assert _shop_bags(stocked_shop, flour) == 40
    assert _bags(flour) == 100
    assert _order(api, flour, key=key).data["status"] == "cancelled"
    assert _shop_bags(stocked_shop, flour) == 40


def test_sales_allow_negative_stock_and_keep_mill_unchanged(auth_client, worker, stocked_shop, flour, make_product):
    other = make_product(name="Другой")
    _price(stocked_shop, other)
    api = auth_client(worker)
    response = api.post("/api/shop-orders/", {
        "idempotency_key": str(uuid4()),
        "items": [{"product": flour.pk, "quantity": 5}, {"product": other.pk, "quantity": 1}],
    }, format="json")
    assert response.status_code == 201, response.data
    assert _shop_bags(stocked_shop, flour) == 35
    assert _shop_bags(stocked_shop, other) == -1
    assert _bags(flour) == 100
    assert _order(api, flour, quantity=41).status_code == 201
    assert _shop_bags(stocked_shop, flour) == -6
    catalog = {row["id"]: row for row in api.get("/api/shop-orders/catalog/").data}
    assert catalog[flour.pk]["stock_bags"] == -6
    assert catalog[other.pk]["stock_bags"] == -1


def test_unpriced_and_archived_products_are_rejected(auth_client, worker, shop, flour):
    ShopStock.objects.create(shop=shop, product=flour, bags=20)
    api = auth_client(worker)
    assert _order(api, flour).data["code"] == "shop_price_missing"
    _price(shop, flour)
    flour.is_active = False
    flour.save(update_fields=["is_active"])
    assert _order(api, flour).status_code == 400
    assert _shop_bags(shop, flour) == 20
    assert not ShopSale.objects.exists()


@pytest.mark.parametrize("quantity", [0, -1, 1.5, 1000001])
def test_sale_rejects_invalid_quantity(auth_client, worker, stocked_shop, flour, quantity):
    assert _order(auth_client(worker), flour, quantity=quantity).status_code == 400
    assert _shop_bags(stocked_shop, flour) == 40


def test_sale_rejects_duplicate_items(auth_client, worker, stocked_shop, flour):
    response = auth_client(worker).post("/api/shop-orders/", {
        "idempotency_key": str(uuid4()),
        "items": [{"product": flour.pk, "quantity": 1}] * 2,
    }, format="json")
    assert response.status_code == 400
    assert not ShopSale.objects.exists()
    assert _shop_bags(stocked_shop, flour) == 40


def test_shops_do_not_see_or_cancel_each_others_sales(auth_client, worker, stocked_shop, flour, user_with_perms, departments):
    from apps.employees.shop import set_employee_shop

    sale_id = _order(auth_client(worker), flour).data["id"]
    other = user_with_perms("other-shop", codes=["orders.create"], department=departments[0])
    other_shop = set_employee_shop(other.employee, other, enabled=True)
    _price(other_shop, flour)
    api = auth_client(other)
    assert api.get("/api/shop-orders/").data == []
    assert api.get("/api/shop-orders/catalog/").data[0]["stock_bags"] == 0
    assert api.post(f"/api/shop-orders/{sale_id}/cancel/").status_code == 404
    assert _order(api, flour, shop=stocked_shop.pk).status_code == 201
    assert _shop_bags(other_shop, flour) == -10
    assert ShopSale.objects.filter(shop=other_shop).get().shop_id == other_shop.pk
    assert _shop_bags(stocked_shop, flour) == 30


def test_sale_optional_client_must_be_accessible(auth_client, worker, stocked_shop, flour, departments):
    from apps.clients.models import Client

    own = Client.objects.create_with_user(first_name="Покупатель", department=departments[0])
    foreign = Client.objects.create_with_user(first_name="Чужой", department=departments[1])
    api = auth_client(worker)
    assert _order(api, flour, client=foreign.pk).status_code == 400
    response = _order(api, flour, client=own.pk)
    assert response.status_code == 201, response.data
    assert response.data["client"] == own.pk
    assert response.data["client_name"] == "Покупатель"
    assert _shop_bags(stocked_shop, flour) == 30


def test_disabled_shop_cannot_sell(auth_client, worker, stocked_shop, flour):
    EmployeeShop.objects.filter(pk=stocked_shop.pk).update(is_active=False)
    assert _order(auth_client(worker), flour).status_code == 404
    assert not ShopSale.objects.exists()
    assert _shop_bags(stocked_shop, flour) == 40


def test_legacy_orders_remain_ordinary_history(auth_client, worker, shop, flour):
    order = Order.objects.create(client=shop.client, created_by=worker, status="shipped")
    assert auth_client(worker).get("/api/shop-orders/").data == []
    assert Order.objects.get(pk=order.pk).status == "shipped"


def test_client_users_have_no_shop(auth_client, client_user):
    assert auth_client(client_user).get("/api/shop-orders/").status_code == 403

def test_repeat_sale_uses_snapshot_after_price_and_product_change(auth_client, worker, stocked_shop, flour):
    api = auth_client(worker)
    key = uuid4()
    original = _order(api, flour, key=key)
    ClientPrice.objects.filter(client=stocked_shop.client, product=flour).update(price=Decimal("1"))
    flour.is_active = False
    flour.name = "Новое название"
    flour.save(update_fields=["is_active", "name"])
    repeated = _order(api, flour, key=key)
    assert repeated.status_code == 201
    assert repeated.data == original.data
    assert _shop_bags(stocked_shop, flour) == 30


def test_explicit_sale_price_without_shop_price_is_snapshotted_and_idempotent(auth_client, worker, shop, flour):
    api = auth_client(worker)
    key = str(uuid4())
    payload = {"idempotency_key": key, "items": [{"product": flour.pk, "quantity": 3, "unit_price": "5000"}]}
    first = api.post("/api/shop-orders/", payload, format="json")
    assert first.status_code == 201, first.data
    assert first.data["items"][0]["unit_price"] == "5000.00"
    assert first.data["total_amount"] == "15000.00"
    assert _shop_bags(shop, flour) == -3
    assert _bags(flour) == 100
    assert not ClientPrice.objects.filter(client=shop.client, product=flour).exists()
    assert not Order.objects.exists()
    assert not Shipment.objects.exists()
    payload["items"][0]["unit_price"] = "5000.00"
    assert api.post("/api/shop-orders/", payload, format="json").data == first.data
    payload["items"][0]["unit_price"] = "5001"
    assert api.post("/api/shop-orders/", payload, format="json").status_code == 400
    assert _shop_bags(shop, flour) == -3
    assert ShopSale.objects.count() == 1
    sale = ShopSale.objects.get()
    assert sale.request_payload["items"][0]["unit_price"] == "5000.00"
    url = f"/api/shop-orders/{sale.pk}/cancel/"
    assert api.post(url).status_code == api.post(url).status_code == 200
    assert _shop_bags(shop, flour) == 0


def test_explicit_sale_price_overrides_shop_price_without_changing_it(auth_client, worker, stocked_shop, flour):
    response = _order(auth_client(worker), flour, items=[{
        "product": flour.pk, "quantity": 2, "unit_price": "50.25",
    }])
    assert response.status_code == 201, response.data
    assert response.data["total_amount"] == "100.50"
    assert ClientPrice.objects.get(client=stocked_shop.client, product=flour).price == Decimal("5000")
    assert _shop_bags(stocked_shop, flour) == 38


@pytest.mark.parametrize("price", ["0", "-1", "0.001", "10000000000", "NaN", "Infinity", "garbage", None, True])
def test_invalid_explicit_sale_price_has_no_side_effects(auth_client, worker, shop, flour, price):
    from rest_framework.exceptions import ValidationError

    from apps.employees.shop_sales import place_shop_sale

    items = [{"product": flour.pk, "quantity": 1, "unit_price": price}]
    response = _order(auth_client(worker), flour, items=items)
    assert response.status_code == 400, response.data
    with pytest.raises(ValidationError):
        place_shop_sale(worker, idempotency_key=uuid4(), items=[{**items[0], "product": flour}])
    assert not ShopSale.objects.exists()
    assert not ShopStock.objects.exists()
