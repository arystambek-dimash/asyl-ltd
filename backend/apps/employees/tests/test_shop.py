from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.catalog.models import ClientPrice
from apps.employees.models import Employee, EmployeeShop
from apps.eventlog.models import EventLog
from apps.orders.models import Order, Payment
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
    """Сотрудник цеха без прав: доступ к «Моему магазину» даёт сам магазин."""
    return user_with_perms("worker", department=departments[0])


@pytest.fixture
def shop(worker, admin_user):
    from apps.employees.shop import set_employee_shop

    return set_employee_shop(worker.employee, admin_user, enabled=True)


def _price(shop, product, price="5000"):
    return ClientPrice.objects.create(client=shop.client, product=product, price=Decimal(price))


def _bags(product, warehouse=None):
    warehouse = warehouse or Warehouse.objects.get(is_default=True)
    return StockItem.objects.get(product=product, warehouse=warehouse).bags


def _order(api, product, quantity=10):
    return api.post("/api/my-shop/orders/", {"items": [{"product": product.pk, "quantity": quantity}]}, format="json")


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
    # Пункт «Мой магазин» пропадает у самого сотрудника, а не у администратора.
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


def test_menu_shows_my_shop_only_with_active_shop(auth_client, worker, user_with_perms):
    assert auth_client(worker).get("/api/auth/me/").data["shop"] is None
    assert auth_client(worker).get("/api/my-shop/").status_code == 404

    from apps.employees.shop import set_employee_shop

    set_employee_shop(worker.employee, worker, enabled=True)
    assert auth_client(worker).get("/api/auth/me/").data["shop"] == {
        "name": "Магазин A B",
        "warehouse_name": Warehouse.objects.get(is_default=True).name,
    }


def test_catalog_shows_shop_prices(auth_client, worker, shop, flour, make_product):
    _price(shop, flour, "5200")
    unpriced = make_product(name="Без цены")
    archived = make_product(name="Архив", is_active=False)

    rows = {row["id"]: row for row in auth_client(worker).get("/api/my-shop/catalog/").data}

    assert rows[flour.pk]["price"] == "5200.00"
    assert rows[unpriced.pk]["price"] is None
    assert archived.pk not in rows
    assert "bags" not in rows[flour.pk]


def test_order_ships_from_warehouse_at_once_into_shop_debt(auth_client, worker, shop, flour):
    _price(shop, flour)
    api = auth_client(worker)

    response = _order(api, flour, quantity=30)

    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data["id"])
    assert order.status == "shipped"
    assert order.client == shop.client
    assert order.created_by == worker
    assert Shipment.objects.get(order=order).bags_loaded == 30
    assert _bags(flour) == 70
    assert response.data["total_amount"] == "150000.00"
    assert response.data["remaining_amount"] == "150000.00"
    assert response.data["cancel_blocker"] == ""
    # Грузчику заказ не попадает: он уже отгружен.
    assert EventLog.objects.filter(event_type="shipment", order=order, user=worker).exists()

    summary = api.get("/api/my-shop/").data
    assert summary["debt_total"] == "150000.00"
    assert summary["month_bags"] == 30
    assert summary["month_amounts"] == {"KZT": "150000.00"}
    assert [row["id"] for row in api.get("/api/my-shop/orders/").data] == [order.pk]


def test_order_from_shop_warehouse(admin_api, auth_client, worker, shop, flour, boss):
    from apps.warehouse.services import receive_stock

    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    receive_stock(flour, 50, boss, warehouse=second)
    admin_api.patch(
        f"/api/employees/{worker.employee.pk}/security/", {"shop": {"enabled": True, "warehouse": second.pk}},
        format="json",
    )
    _price(shop, flour)

    assert _order(auth_client(worker), flour, quantity=5).status_code == 201
    assert _bags(flour, second) == 45
    assert _bags(flour) == 100


def test_product_without_shop_price_is_not_ordered(auth_client, worker, shop, flour):
    response = _order(auth_client(worker), flour)

    assert response.status_code == 400
    assert response.data["code"] == "shop_price_missing"
    assert not Order.objects.exists()
    assert _bags(flour) == 100


def test_worker_cancels_own_order_within_an_hour(auth_client, worker, shop, flour):
    _price(shop, flour)
    api = auth_client(worker)
    order_id = _order(api, flour, quantity=10).data["id"]

    response = api.post(f"/api/my-shop/orders/{order_id}/cancel/")

    assert response.status_code == 200, response.data
    assert response.data["status"] == "cancelled"
    assert response.data["total_amount"] is None
    assert _bags(flour) == 100
    assert api.get("/api/my-shop/").data["debt_total"] == "0.00"


def test_cancel_closes_after_an_hour(auth_client, worker, shop, flour):
    _price(shop, flour)
    api = auth_client(worker)
    order_id = _order(api, flour).data["id"]
    Shipment.objects.filter(order_id=order_id).update(shipped_at=timezone.now() - timedelta(hours=2))

    assert "больше часа" in api.get("/api/my-shop/orders/").data[0]["cancel_blocker"]
    response = api.post(f"/api/my-shop/orders/{order_id}/cancel/")
    assert response.status_code == 400
    assert Order.objects.get(pk=order_id).status == "shipped"


def test_cancel_refused_when_order_has_money(auth_client, worker, shop, flour):
    _price(shop, flour)
    api = auth_client(worker)
    order_id = _order(api, flour).data["id"]
    Payment.objects.create(order_id=order_id, amount=Decimal("1000"), method="cash", status="confirmed")

    assert "оплата" in api.get("/api/my-shop/orders/").data[0]["cancel_blocker"]
    assert api.post(f"/api/my-shop/orders/{order_id}/cancel/").status_code == 400
    assert Order.objects.get(pk=order_id).status == "shipped"


def test_shops_do_not_see_each_others_orders(auth_client, worker, shop, flour, user_with_perms, departments):
    from apps.employees.shop import set_employee_shop

    _price(shop, flour)
    order_id = _order(auth_client(worker), flour).data["id"]
    other = user_with_perms("other", department=departments[0])
    set_employee_shop(other.employee, other, enabled=True)
    api = auth_client(other)

    assert api.get("/api/my-shop/orders/").data == []
    assert api.post(f"/api/my-shop/orders/{order_id}/cancel/").status_code == 404


def test_disabled_shop_cannot_order(auth_client, worker, shop, flour):
    _price(shop, flour)
    EmployeeShop.objects.filter(pk=shop.pk).update(is_active=False)

    assert _order(auth_client(worker), flour).status_code == 404
    assert not Order.objects.exists()


def test_client_users_have_no_shop(auth_client, client_user):
    assert auth_client(client_user).get("/api/my-shop/").status_code == 403


# --- Без грузчика и без «на себя» -------------------------------------------


@pytest.fixture
def cashier_with_shop(user_with_perms, departments, admin_user):
    """Сотрудник с правами кассы и прайса, у которого есть свой магазин."""
    from apps.employees.shop import set_employee_shop

    user = user_with_perms(
        "cashier-shop", department=departments[0],
        codes=["clients.view", "clients.set_price", "payments.create", "payments.confirm", "orders.view"],
    )
    return user, set_employee_shop(user.employee, admin_user, enabled=True)


def test_loader_does_not_see_shop_sales(auth_client, user_with_perms, worker, shop, flour, admin_user):
    _price(shop, flour)
    sold = _order(auth_client(worker), flour, quantity=3).data["id"]
    assert Order.objects.get(pk=sold).is_shop_sale
    # Обычный заказ фурой на того же клиента-магазина — работа грузчика, как всегда.
    truck = Order.objects.create(client=shop.client, status="confirmed", created_by=admin_user)
    loader = auth_client(user_with_perms("trucks-loader", codes=["loader.view", "loader.confirm", "loader.trucks"]))

    assert [row["id"] for row in loader.get("/api/loader/queue/").data] == [truck.pk]
    assert loader.get("/api/loader/history/").data == []
    assert loader.get("/api/loader/day-summary/").data["bags"] == 0
    assert loader.get(f"/api/loader/orders/{sold}/waybill/").status_code == 404


def test_employee_does_not_set_prices_of_own_shop(auth_client, admin_api, cashier_with_shop, flour):
    user, own = cashier_with_shop
    body = {"prices": [{"product": flour.pk, "currency": "KZT", "price": "1"}]}

    response = auth_client(user).put(f"/api/clients/{own.client_id}/prices/", body, format="json")

    assert response.status_code == 403
    assert response.data["code"] == "own_shop"
    assert not ClientPrice.objects.filter(client=own.client).exists()
    assert admin_api.put(f"/api/clients/{own.client_id}/prices/", body, format="json").status_code == 200


def test_employee_does_not_take_payment_for_own_shop(auth_client, cashier_with_shop, flour, boss):
    from apps.orders import services

    user, own = cashier_with_shop
    _price(own, flour)
    sold = Order.objects.get(pk=_order(auth_client(user), flour, quantity=2).data["id"])
    url = f"/api/orders/{sold.pk}/payments/"

    response = auth_client(user).post(url, {"amount": "10000", "method": "cash"}, format="json")

    assert response.status_code == 403
    assert response.data["code"] == "own_shop"
    assert not Payment.objects.filter(order=sold).exists()
    # Деньги, которые принял другой, сам сотрудник тоже не подтверждает и цену не правит.
    received = services.add_payment(sold, "10000", boss, method="bank")
    with pytest.raises(PermissionDenied):
        services.accountant_confirm_payment(received, user)
    with pytest.raises(PermissionDenied):
        services.correct_order_prices(sold, user, total_amount="1")
    # Другой сотрудник — как с любым клиентом.
    assert services.accountant_confirm_payment(received, boss).status == "confirmed"
