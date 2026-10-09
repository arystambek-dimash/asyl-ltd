"""Страница «Кладовщик»: /api/storekeeper/returns/ — приёмка возвратов товара, без денег."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.goods_returns import record_goods_return
from apps.orders.models import GoodsReturn, Order, OrderItem
from apps.orders.services import record_staff_payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db

URL = "/api/storekeeper/returns/"
MONEY_KEYS = {"amounts", "amount", "lines", "settlement", "settlement_label", "unit_price", "currency"}


@pytest.fixture
def storekeeper(user_with_perms):
    """Только права кладовщика: ни заказов, ни кассы."""
    return user_with_perms("storekeeper", codes=["storekeeper.view", "storekeeper.confirm"])


@pytest.fixture
def manager(user_with_perms, departments):
    return user_with_perms("mill-manager", codes=["orders.view", "orders.edit"], department=departments[0])


def _client(department, name="Дан Агро", phone="+7 (705) 565-65-65"):
    return Client.objects.create_with_user(first_name=name, phone=phone, department=department)


def _flour(name="Первый сорт DIKHAN 50кг", color="Blue"):
    product, _ = Product.objects.get_or_create(name=name, color=color, weight_kg=Decimal("50"))
    return product


def _shipped(client, items):
    order = Order.objects.create(client=client, status="shipped", department=client.department.code)
    for product, quantity, price in items:
        OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price=Decimal(price))
    Shipment.objects.create(order=order, shipped_at=timezone.now())
    return order


def _pending(client, user, lines):
    """Возврат «Ждёт приёмки»: ``lines`` — [(товар, мешков)]."""
    plan = record_goods_return(
        client, user, settlement="debt", warehouse=None,
        lines=[{"product": product.pk, "bags": bags} for product, bags in lines],
    )
    return GoodsReturn.objects.get(pk=plan["return_id"])


def _get(api, **params):
    response = api.get(URL, params)
    assert response.status_code == 200, response.data
    return response.data


def _confirm(api, goods_return, item, accepted):
    return api.post(f"{URL}{goods_return.pk}/items/{item.pk}/", {"accepted_bags": accepted}, format="json")


def _close(api, goods_return):
    return api.post(f"{URL}{goods_return.pk}/close/")


def _reopen(api, goods_return):
    return api.post(f"{URL}{goods_return.pk}/reopen/")


def _cancel(api, goods_return):
    return api.post(f"{URL}{goods_return.pk}/cancel/")


def test_storekeeper_accepts_lowers_and_closes_a_return_without_seeing_money(
    api_as, storekeeper, manager, departments,
):
    client = _client(departments[0])
    dikhan = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")
    order = _shipped(client, [(dikhan, 20, "3000"), (korol, 10, "2000")])
    goods_return = _pending(client, manager, [(dikhan, 10), (korol, 6)])
    first, second = goods_return.items.order_by("id")
    api = api_as(storekeeper)

    [row] = _get(api)
    assert row["created_at"] == timezone.localtime(goods_return.created_at).isoformat()
    assert {key: value for key, value in row.items() if key != "created_at"} == {
        "id": goods_return.pk,
        "client_name": "Дан Агро",
        "warehouse_name": get_default_warehouse().name,
        "created_by_name": "A B",
        "status": "pending",
        "status_label": "Ждёт приёмки",
        "accepted_by_name": None,
        "accepted_at": None,
        "bags": 16,
        "accepted_bags": None,
        "can_reopen": False,
        "items": [
            {"id": first.pk, "product_label": "Первый сорт DIKHAN 50кг", "bags": 10, "accepted_bags": None},
            {"id": second.pk, "product_label": "Второй сорт KOROL 50кг", "bags": 6, "accepted_bags": None},
        ],
    }

    confirmed = _confirm(api, goods_return, first, 10)
    assert confirmed.status_code == 200, confirmed.data
    assert (confirmed.data["accepted_bags"], confirmed.data["items"][0]["accepted_bags"]) == (10, 10)
    unchecked = _close(api, goods_return)
    assert unchecked.status_code == 400
    assert unchecked.data["code"] == "items_unchecked"

    lowered = _confirm(api, goods_return, second, 5)  # привезли меньше
    assert lowered.data["accepted_bags"] == 15
    closed = _close(api, goods_return)

    assert closed.status_code == 200, closed.data
    assert (closed.data["status"], closed.data["status_label"]) == ("partial", "Частично возвращено")
    assert (closed.data["accepted_by_name"], closed.data["bags"], closed.data["accepted_bags"]) == ("A B", 16, 15)
    assert closed.data["can_reopen"] is True
    assert MONEY_KEYS.isdisjoint(closed.data) and MONEY_KEYS.isdisjoint(row)
    assert dict(OrderItem.objects.filter(order=order).values_list("product_id", "returned_quantity")) == {
        dikhan.pk: 10, korol.pk: 5,
    }
    assert dict(StockItem.objects.values_list("product_id", "bags")) == {dikhan.pk: 10, korol.pk: 5}
    assert _get(api) == []
    assert [history["id"] for history in _get(api, state="closed")] == [goods_return.pk]
    again = _close(api, goods_return)
    assert again.status_code == 400
    assert again.data["code"] == "goods_return_not_pending"
    late = _confirm(api, goods_return, first, 1)
    assert late.status_code == 400
    assert late.data["code"] == "goods_return_not_pending"


def test_count_outside_the_requested_bags_is_refused(api_as, storekeeper, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 20, "3000")])
    goods_return = _pending(client, manager, [(flour, 4)])
    item = goods_return.items.get()
    api = api_as(storekeeper)

    too_many = _confirm(api, goods_return, item, 5)
    as_text = _confirm(api, goods_return, item, "4")

    assert (too_many.status_code, too_many.data["code"]) == (400, "goods_return_bad_count")
    assert (as_text.status_code, as_text.data["code"]) == (400, "goods_return_bad_count")
    other = _pending(client, manager, [(flour, 1)])
    assert _confirm(api, other, item, 1).status_code == 404  # мука чужого возврата


def test_close_explains_when_the_debt_is_gone(api_as, storekeeper, manager, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    goods_return = _pending(client, manager, [(flour, 4)])
    api = api_as(storekeeper)
    _confirm(api, goods_return, goods_return.items.get(), 4)
    record_staff_payment(order, Decimal("10000"), boss, method="cash")

    response = _close(api, goods_return)

    assert response.status_code == 400
    assert response.data["code"] == "goods_return_no_longer_fits"
    assert "отменить" in response.data["detail"]
    assert OrderItem.objects.get().returned_quantity == 0


def test_pending_oldest_first_and_history_newest_closed_first(api_as, storekeeper, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 50, "1000")])
    old, mid, new = (_pending(client, manager, [(flour, 1)]) for _ in range(3))
    now = timezone.now()
    for goods_return, minutes in ((old, 30), (mid, 20), (new, 10)):
        GoodsReturn.objects.filter(pk=goods_return.pk).update(created_at=now - timedelta(minutes=minutes))
    api = api_as(storekeeper)

    assert [row["id"] for row in _get(api, state="pending")] == [old.pk, mid.pk, new.pk]
    for goods_return in (new, old):  # «new» закрыт раньше «old»
        _confirm(api, goods_return, goods_return.items.get(), 1)
        assert _close(api, goods_return).status_code == 200
    GoodsReturn.objects.filter(pk=mid.pk).update(status="cancelled", accepted_at=now + timedelta(minutes=5))

    assert [row["id"] for row in _get(api, state="closed")] == [mid.pk, old.pk, new.pk]
    assert [row["id"] for row in _get(api)] == []
    bad = api.get(URL, {"state": "all"})
    assert (bad.status_code, bad.data["code"]) == (400, "bad_state")


def test_search_by_client_or_return_number_and_pages(api_as, storekeeper, manager, departments):
    berek = _client(departments[0], "Береке", phone="+7 (701) 111-22-33")
    dan = _client(departments[0], "Дан Агро", phone="+7 (702) 444-55-66")
    flour = _flour()
    _shipped(berek, [(flour, 10, "1000")])
    _shipped(dan, [(flour, 10, "1000")])
    first = _pending(berek, manager, [(flour, 1)])
    second = _pending(dan, manager, [(flour, 1)])
    api = api_as(storekeeper)

    assert [row["id"] for row in _get(api, search="Берек")] == [first.pk]
    assert [row["id"] for row in _get(api, search="444-55")] == [second.pk]
    assert [row["id"] for row in _get(api, search=f"#{second.pk}")] == [second.pk]
    page = _get(api, page=1, page_size=1)
    assert (page["count"], [row["id"] for row in page["results"]]) == (2, [first.pk])


def test_storekeeper_sees_only_own_department(api_as, user_with_perms, manager, departments):
    mill, city = departments
    client = _client(mill)
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = _pending(client, manager, [(flour, 1)])
    city_storekeeper = user_with_perms(
        "city-storekeeper", codes=["storekeeper.view", "storekeeper.confirm"], department=city,
    )
    api = api_as(city_storekeeper)

    assert _get(api) == []
    assert _confirm(api, goods_return, goods_return.items.get(), 1).status_code == 404
    assert _close(api, goods_return).status_code == 404
    assert _cancel(api, goods_return).status_code == 404
    assert _reopen(api, goods_return).status_code == 404


def test_permissions_split_the_manager_and_the_storekeeper(api_as, user_with_perms, storekeeper, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = _pending(client, manager, [(flour, 1)])
    item = goods_return.items.get()
    viewer = user_with_perms("storekeeper-viewer", codes=["storekeeper.view"])

    manager_api = api_as(manager)
    assert manager_api.get(URL).status_code == 403
    assert _confirm(manager_api, goods_return, item, 1).status_code == 403
    assert _close(manager_api, goods_return).status_code == 403
    assert _confirm(api_as(viewer), goods_return, item, 1).status_code == 403
    assert _close(api_as(viewer), goods_return).status_code == 403
    assert _cancel(api_as(viewer), goods_return).status_code == 403
    assert _cancel(manager_api, goods_return).status_code == 403
    keeper = api_as(storekeeper)
    _confirm(keeper, goods_return, item, 1)
    assert _close(keeper, goods_return).status_code == 200
    assert _reopen(api_as(viewer), goods_return).status_code == 403
    assert _reopen(manager_api, goods_return).status_code == 403
    goods_return.refresh_from_db()
    assert goods_return.status == "full"
    keeper_api = api_as(storekeeper)
    body = {"settlement": "debt", "lines": [{"product": flour.pk, "bags": 1}], "preview": True}
    assert keeper_api.post(f"/api/clients/{client.pk}/goods-return/", body, format="json").status_code == 403
    assert keeper_api.post(f"/api/orders/returns/{goods_return.pk}/cancel/").status_code == 403
    assert keeper_api.get("/api/orders/returns/").status_code == 403


def test_query_count_does_not_grow_with_returns(count_queries, storekeeper, manager, departments):
    flour = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")

    def add_returns(n):
        client = _client(departments[0], f"Клиент {n}", phone=f"+7 (700) 000-00-{n:02d}")
        _shipped(client, [(flour, 10, "1000"), (korol, 10, "1000")])
        _pending(client, manager, [(flour, 1), (korol, 2)])

    add_returns(0)
    few = count_queries(storekeeper, f"{URL}?page=1")
    for n in range(1, 6):
        add_returns(n)
    many = count_queries(storekeeper, f"{URL}?page=1")

    assert few == many


def test_storekeeper_corrects_a_closed_return_and_closes_it_again(api_as, storekeeper, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 20, "3000")])
    goods_return = _pending(client, manager, [(flour, 10)])
    item = goods_return.items.get()
    api = api_as(storekeeper)
    _confirm(api, goods_return, item, 10)
    assert _close(api, goods_return).status_code == 200  # ошибся: на деле привезли 8

    reopened = _reopen(api, goods_return)

    assert reopened.status_code == 200, reopened.data
    assert (reopened.data["status"], reopened.data["status_label"]) == ("pending", "Ждёт приёмки")
    assert (reopened.data["accepted_by_name"], reopened.data["accepted_at"]) == (None, None)
    assert (reopened.data["bags"], reopened.data["accepted_bags"]) == (10, 10)  # прежние числа
    assert reopened.data["can_reopen"] is False
    assert MONEY_KEYS.isdisjoint(reopened.data)
    assert [row["id"] for row in _get(api)] == [goods_return.pk]
    assert _get(api, state="closed") == []
    assert OrderItem.objects.get(order=order).returned_quantity == 0
    assert StockItem.objects.get(product=flour).bags == 0
    twice = _reopen(api, goods_return)
    assert (twice.status_code, twice.data["code"]) == (400, "goods_return_not_closed")

    assert _confirm(api, goods_return, item, 8).data["accepted_bags"] == 8
    closed = _close(api, goods_return)

    assert closed.status_code == 200, closed.data
    assert (closed.data["status"], closed.data["accepted_bags"], closed.data["accepted_by_name"]) == (
        "partial", 8, "A B",
    )
    assert OrderItem.objects.get(order=order).returned_quantity == 8
    assert StockItem.objects.get(product=flour).bags == 8


def test_storekeeper_cancels_a_pending_return(api_as, storekeeper, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 20, "3000")])
    goods_return = _pending(client, manager, [(flour, 10)])
    api = api_as(storekeeper)

    cancelled = _cancel(api, goods_return)

    assert cancelled.status_code == 200, cancelled.data
    assert (cancelled.data["status"], cancelled.data["status_label"]) == ("cancelled", "Отменён")
    assert cancelled.data["accepted_by_name"] == "A B" and cancelled.data["accepted_at"]
    assert cancelled.data["can_reopen"] is True  # свою отмену кладовщик может исправить
    assert MONEY_KEYS.isdisjoint(cancelled.data)
    assert _get(api) == []
    assert [row["id"] for row in _get(api, state="closed")] == [goods_return.pk]
    again = _cancel(api, goods_return)
    assert (again.status_code, again.data["code"]) == (400, "goods_return_not_pending")
    assert OrderItem.objects.get().returned_quantity == 0


def test_storekeeper_cannot_fix_a_return_the_manager_cancelled(api_as, storekeeper, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 20, "3000")])
    goods_return = _pending(client, manager, [(flour, 10)])
    cancelled = api_as(manager).post(f"/api/orders/returns/{goods_return.pk}/cancel/")
    assert cancelled.status_code == 200, cancelled.data
    api = api_as(storekeeper)

    [row] = _get(api, state="closed")
    refused = _reopen(api, goods_return)

    assert (row["status"], row["can_reopen"]) == ("cancelled", False)
    assert (refused.status_code, refused.data["code"]) == (400, "goods_return_cannot_reopen")
    goods_return.refresh_from_db()
    assert goods_return.status == "cancelled"
