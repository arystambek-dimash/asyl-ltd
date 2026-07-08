"""Приёмочные проверки ТЗ «Выездной отдел продаж» (раздел 6).

Отдел 2 «Сити»: менеджер видит только своих клиентов и заказы,
руководитель — всё со столбцом «Отдел», заявки менеджера подтверждает
бухгалтер, оплата принимается с выезда до отгрузки.
"""
import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


_seq = [0]


def _product(bags=500):
    from apps.warehouse.models import StockItem
    _seq[0] += 1
    p = Product.objects.create(
        name=f"P{_seq[0]}", color="Red", weight_kg="50", price="100.00")
    if bags:
        StockItem.objects.create(product=p, bags=bags)
    return p


def _client(dept="main", manager=None, name="A"):
    return Client.objects.create(
        first_name=name, last_name="B", phone="x", department=dept, manager=manager)


def _order(client, qty=1, status="pending"):
    p = _product()
    o = Order.objects.create(client=client, status=status,
                             department=client.department)
    OrderItem.objects.create(order=o, product=p, quantity=qty, unit_price="100.00")
    return o


def test_manager_sees_only_own_clients(dept2_manager, user_with_perms):
    other = user_with_perms("citymanager2", codes=["dept2.view", "dept2.create"])
    mine = _client("field", dept2_manager, name="Mine")
    _client("field", other, name="Other")
    _client("main", name="MainDept")
    r = _api(dept2_manager).get("/api/clients/")
    assert r.status_code == 200
    assert [row["id"] for row in r.data] == [mine.id]


def test_manager_sees_only_own_orders(dept2_manager, user_with_perms):
    other = user_with_perms("citymanager2", codes=["dept2.view", "dept2.create"])
    mine = _order(_client("field", dept2_manager))
    _order(_client("field", other))
    _order(_client("main"))
    r = _api(dept2_manager).get("/api/orders/")
    assert r.status_code == 200
    assert [row["id"] for row in r.data] == [mine.id]


def test_dept1_staff_do_not_see_field_clients(manager):
    """Клиент Отдела 2 не виден в списках Отдела 1 (приёмка №2)."""
    _client("field", name="Fld")
    main = _client("main", name="Mn")
    r = _api(manager).get("/api/clients/")
    assert [row["id"] for row in r.data] == [main.id]


def test_boss_sees_all_with_department_column(boss, user_with_perms):
    """Руководитель видит заказы обоих отделов, в данных есть «Отдел» (приёмка №3)."""
    from apps.rbac.models import Permission
    # Начальнику по пресету положено видеть всё: докидываем dept2.view_all.
    emp = boss.employee
    for code in ("orders.view", "dept2.view_all"):
        p, _ = Permission.objects.get_or_create(
            code=code, defaults={"section": code.split(".")[0],
                                 "action": code.split(".")[1], "label": code})
        emp.permissions.add(p)
    o1 = _order(_client("main"))
    o2 = _order(_client("field"))
    r = _api(boss).get("/api/orders/")
    rows = {row["id"]: row["department"] for row in r.data}
    assert rows[o1.id] == "main"
    assert rows[o2.id] == "field"


def test_manager_creates_client_forced_to_field(dept2_manager):
    """Клиент менеджера автоматически помечается как клиент Отдела 2 (приёмка №2)."""
    r = _api(dept2_manager).post("/api/clients/", {
        "first_name": "Новый", "last_name": "Клиент", "phone": "+7",
        "department": "main",  # подмена отдела игнорируется сервером
    }, format="json")
    assert r.status_code == 201
    c = Client.objects.get(pk=r.data["id"])
    assert c.department == "field"
    assert c.manager == dept2_manager


def test_manager_order_stays_pending_for_accountant(dept2_manager):
    """Заявка менеджера с ценами ждёт подтверждения бухгалтером."""
    c = _client("field", dept2_manager)
    p = _product()
    r = _api(dept2_manager).post("/api/orders/", {
        "client": c.id, "items": [{"product": p.id, "quantity": 2}],
        "prices": {str(p.id): "150.00"},
    }, format="json")
    assert r.status_code == 201
    o = Order.objects.get(pk=r.data["id"])
    assert o.status == "pending"
    assert o.department == "field"
    assert o.items.first().unit_price == Decimal("150.00")


def test_manager_cannot_order_for_foreign_client(dept2_manager):
    foreign = _client("main")
    p = _product()
    r = _api(dept2_manager).post("/api/orders/", {
        "client": foreign.id, "items": [{"product": p.id, "quantity": 1}],
    }, format="json")
    assert r.status_code == 400


def test_accountant_confirms_manager_order_without_reentering_prices(accountant, dept2_manager):
    o = _order(_client("field", dept2_manager))
    r = _api(accountant).post(f"/api/orders/{o.id}/confirm/", {}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "confirmed"


def test_field_order_accepts_payment_before_shipping(dept2_manager):
    """Менеджер принимает наличные с выезда до отгрузки (ТЗ 4.5)."""
    o = _order(_client("field", dept2_manager), status="confirmed")
    r = _api(dept2_manager).post(f"/api/orders/{o.id}/payments/",
                                 {"amount": "100.00", "stage": "received"}, format="json")
    assert r.status_code == 201
    pay = Payment.objects.get(pk=r.data["id"])
    assert pay.status == "received"
    assert pay.received_by == dept2_manager


def test_main_order_payment_still_requires_shipped(accountant):
    o = _order(_client("main"), status="confirmed")
    r = _api(accountant).post(f"/api/orders/{o.id}/payments/",
                              {"amount": "100.00"}, format="json")
    assert r.status_code == 400


def test_payments_queue_for_accountant_and_cashier(accountant, cashier, dept2_manager, settle_payment):
    from apps.orders import services
    o = _order(_client("field", dept2_manager), status="confirmed")
    pay = services.add_payment(o, "100", dept2_manager)  # received
    r = _api(accountant).get("/api/orders/payments-queue/?stage=received")
    assert [row["id"] for row in r.data] == [pay.id]
    assert r.data[0]["department"] == "field"
    services.accountant_confirm_payment(pay, accountant)
    r = _api(cashier).get("/api/orders/payments-queue/?stage=accountant_ok")
    assert [row["id"] for row in r.data] == [pay.id]
    r = _api(cashier).post(f"/api/orders/{o.id}/payments/{pay.id}/cashier-confirm/")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.payment_status == "settled"


def test_manager_cannot_access_queue(dept2_manager):
    r = _api(dept2_manager).get("/api/orders/payments-queue/")
    assert r.status_code == 403


def test_department_filter_on_orders(cashier):
    o1 = _order(_client("main"))
    o2 = _order(_client("field"))
    r = _api(cashier).get("/api/orders/?department=field")
    assert [row["id"] for row in r.data] == [o2.id]
    r = _api(cashier).get("/api/orders/?department=main")
    assert [row["id"] for row in r.data] == [o1.id]
