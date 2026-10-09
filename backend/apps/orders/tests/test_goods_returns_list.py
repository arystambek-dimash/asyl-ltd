"""«Заказы → Возвраты»: GET /api/orders/returns/ — новые возвраты (только склад) и старые, с деньгами по заказам."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.goods_returns import close_goods_return, confirm_goods_return_item, create_goods_return
from apps.orders.models import GoodsReturn, GoodsReturnItem, GoodsReturnLine, Order, OrderItem
from apps.shipments.models import Shipment
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db

URL = "/api/orders/returns/"


@pytest.fixture
def viewer(user_with_perms):
    """Сотрудник без отдела: видит возвраты всех отделов."""
    return user_with_perms("returns-viewer", codes=["orders.view"])


def _client(department, name="Клиент"):
    # Телефон без цифр: поиск по номеру заказа не должен совпасть с телефоном.
    return Client.objects.create_with_user(first_name=name, phone="x", department=department)


def _flour(name="Первый сорт DIKHAN 50кг"):
    product, _ = Product.objects.get_or_create(name=name, color="Blue", weight_kg=Decimal("50"))
    return product


def _shipped(client, *, bags=100, price="3000", currency="KZT", department=None, product=None):
    order = Order.objects.create(
        client=client, status="shipped", currency=currency,
        department=client.department.code if department is None else department,
    )
    OrderItem.objects.create(order=order, product=product or _flour(), quantity=bags, unit_price=Decimal(price))
    return order


def _return(client, lines, *, settlement="debt", user=None, at=None):
    """Старый возврат, проведённый с деньгами: ``lines`` — [(заказ, мешков)] по позиции заказа."""
    goods_return = GoodsReturn.objects.create(
        client=client, settlement=settlement, warehouse=get_default_warehouse(), created_by=user, status="full",
    )
    for order, bags in lines:
        item = order.items.get()
        GoodsReturnLine.objects.create(
            goods_return=goods_return, order_item=item, bags=bags,
            unit_price=item.unit_price, amount=item.unit_price * bags,
        )
    if at is not None:
        GoodsReturn.objects.filter(pk=goods_return.pk).update(created_at=at)
    return goods_return


def _get(api_as, user, **params):
    response = api_as(user).get(URL, params)
    assert response.status_code == 200, response.data
    return response.data


def _ids(rows):
    return [row["id"] for row in rows]


def test_row_follows_the_contract_and_newest_comes_first(api_as, viewer, departments, make_user):
    mill, city = departments
    client = _client(mill, "Дан Агро")
    old_order = _shipped(client, price="4700")
    new_order = _shipped(client, department="city", price="4800")
    author = make_user("returns-author")  # ФИО «A B»
    now = timezone.now()
    older = _return(client, [(old_order, 10)], at=now - timedelta(days=1))
    newest = _return(client, [(old_order, 30), (new_order, 5)], settlement="cash", user=author, at=now)
    same_moment = _return(client, [(old_order, 1)], at=now)  # то же время — выше по id

    rows = _get(api_as, viewer)

    assert _ids(rows) == [same_moment.pk, newest.pk, older.pk]
    row = rows[1]
    assert row["created_at"] == timezone.localtime(now).isoformat()
    assert {key: value for key, value in row.items() if key != "created_at"} == {
        "id": newest.pk,
        "client_name": "Дан Агро",
        "settlement_label": "Из кассы",
        "warehouse_name": get_default_warehouse().name,
        "created_by_name": "A B",
        "status": "full",
        "status_label": "Полностью возвращено",
        "accepted_by_name": None,
        "accepted_at": None,
        "items": [],
        "amounts": {"KZT": "165000.00"},
        "lines": [
            {
                "order": new_order.pk, "order_department": "city", "order_department_name": city.name,
                "product_label": "Первый сорт DIKHAN 50кг", "bags": 5,
                "unit_price": "4800.00", "amount": "24000.00", "currency": "KZT",
            },
            {
                "order": old_order.pk, "order_department": "mill", "order_department_name": mill.name,
                "product_label": "Первый сорт DIKHAN 50кг", "bags": 30,
                "unit_price": "4700.00", "amount": "141000.00", "currency": "KZT",
            },
        ],
    }
    assert (rows[2]["settlement_label"], rows[2]["created_by_name"]) == ("В счёт долга", None)


def test_pages_like_the_orders_list(api_as, viewer, departments):
    client = _client(departments[0])
    order = _shipped(client)
    first = _return(client, [(order, 1)])
    second = _return(client, [(order, 2)])

    page = _get(api_as, viewer, page=1, page_size=1)

    assert page["count"] == 2
    assert _ids(page["results"]) == [second.pk]
    assert _ids(_get(api_as, viewer, page=2, page_size=1)["results"]) == [first.pk]


def test_trash_lines_are_hidden_and_a_fully_trashed_return_is_absent(api_as, viewer, departments):
    client = _client(departments[0])
    live = _shipped(client, price="1000")
    trashed = _shipped(client, price="2000")
    mixed = _return(client, [(live, 3), (trashed, 4)])
    gone = _return(client, [(trashed, 5)])
    Order.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())

    rows = _get(api_as, viewer)

    assert _ids(rows) == [mixed.pk]
    assert gone.pk not in _ids(rows)
    assert [(line["order"], line["bags"]) for line in rows[0]["lines"]] == [(live.pk, 3)]
    assert rows[0]["amounts"] == {"KZT": "3000.00"}
    assert _get(api_as, viewer, search=str(trashed.pk)) == []


def test_department_employee_sees_only_own_department(api_as, user_with_perms, departments):
    mill, city = departments
    mill_client = _client(mill, "Мельничный")
    city_client = _client(city, "Городской")
    own = _return(mill_client, [(_shipped(mill_client), 2)])
    _return(city_client, [(_shipped(city_client), 3)])
    pinned = user_with_perms("mill-returns", codes=["orders.view"], department=mill)

    rows = _get(api_as, pinned)

    assert _ids(rows) == [own.pk]
    assert _get(api_as, pinned, department="city") == []


def test_department_filter_keeps_only_lines_of_that_department(api_as, viewer, departments):
    mill, city = departments
    client = _client(mill)
    mill_order = _shipped(client, price="1000")
    city_order = _shipped(client, department="city", price="2000")
    no_own_department = _shipped(client, department="", price="500")  # отдел клиента
    both = _return(client, [(mill_order, 1), (city_order, 2), (no_own_department, 4)])
    only_mill = _return(client, [(mill_order, 7)])

    city_rows = _get(api_as, viewer, department="city")
    mill_rows = _get(api_as, viewer, department="mill")

    assert _ids(city_rows) == [both.pk]
    assert [(line["order"], line["bags"]) for line in city_rows[0]["lines"]] == [(city_order.pk, 2)]
    assert city_rows[0]["amounts"] == {"KZT": "4000.00"}
    assert _ids(mill_rows) == [only_mill.pk, both.pk]
    assert [
        (line["order"], line["order_department"], line["order_department_name"])
        for line in mill_rows[1]["lines"]
    ] == [(no_own_department.pk, "mill", mill.name), (mill_order.pk, "mill", mill.name)]
    assert mill_rows[1]["amounts"] == {"KZT": "3000.00"}


def test_search_by_client_name_and_order_number(api_as, viewer, departments):
    berek = _client(departments[0], "Береке")
    dan = _client(departments[0], "Дан Агро")
    other = _return(berek, [(_shipped(berek), 1)])
    # Создан позже: его номер не может быть частью номера заказа «Береке».
    target_order = _shipped(dan)
    target = _return(dan, [(_shipped(dan), 2), (target_order, 3)])

    assert _ids(_get(api_as, viewer, search="Агро")) == [target.pk]
    assert _ids(_get(api_as, viewer, search="Берек")) == [other.pk]
    assert _ids(_get(api_as, viewer, search=str(target_order.pk))) == [target.pk]
    found = _get(api_as, viewer, search=f"#{target_order.pk}")
    assert _ids(found) == [target.pk]
    assert len(found[0]["lines"]) == 2  # поиск выбирает возврат, строки — все видимые


def test_amounts_are_split_by_currency_never_summed(api_as, viewer, departments):
    client = _client(departments[0])
    tenge = _shipped(client, price="4700")
    dollars = _shipped(client, price="30", currency="USD")
    _return(client, [(tenge, 30), (dollars, 2)])

    [row] = _get(api_as, viewer)

    assert row["amounts"] == {"KZT": "141000.00", "USD": "60.00"}
    assert [(line["currency"], line["amount"]) for line in row["lines"]] == [
        ("USD", "60.00"), ("KZT", "141000.00"),
    ]


def test_date_filter_uses_the_local_day_of_the_return(api_as, viewer, departments):
    client = _client(departments[0])
    order = _shipped(client)
    # 20:30 UTC 4-го — уже 5-е по Алматы; 12:00 UTC 4-го — ещё 4-е.
    late = _return(client, [(order, 1)], at=datetime(2026, 10, 4, 20, 30, tzinfo=dt_timezone.utc))
    early = _return(client, [(order, 1)], at=datetime(2026, 10, 4, 12, 0, tzinfo=dt_timezone.utc))

    assert _ids(_get(api_as, viewer, date_from="2026-10-05", date_to="2026-10-05")) == [late.pk]
    assert _ids(_get(api_as, viewer, date_to="2026-10-04")) == [early.pk]
    assert _ids(_get(api_as, viewer, date_from="2026-10-04")) == [late.pk, early.pk]
    response = api_as(viewer).get(URL, {"date_from": "05.10.2026"})
    assert response.status_code == 400
    assert response.data["code"] == "bad_date"


def test_requires_the_orders_view_permission(api_as, user_with_perms):
    outsider = user_with_perms("no-orders", codes=["payments.view"])

    assert api_as(outsider).get(URL).status_code == 403


def _pending(client, user, bags=4, flour=None):
    """Новый возврат, который менеджер создал и который ждёт кладовщика."""
    flour = flour or _flour()
    return create_goods_return(client, user, warehouse=None, lines=[{"product": flour.pk, "bags": bags}])


@pytest.fixture
def returns_manager(user_with_perms, departments):
    return user_with_perms("returns-manager", codes=["orders.view", "orders.edit"], department=departments[0])


def test_new_return_is_listed_while_pending_and_after_the_storekeeper_closes_it_without_money(
    api_as, viewer, returns_manager, user_with_perms, departments,
):
    client = _client(departments[0], "Дан Агро")
    flour = _flour()
    order = _shipped(client, bags=40, price="3500", product=flour)
    Shipment.objects.create(order=order, shipped_at=timezone.now())
    goods_return = _pending(client, returns_manager, bags=4, flour=flour)

    [pending] = _get(api_as, viewer)
    assert (pending["id"], pending["status"], pending["status_label"]) == (goods_return.pk, "pending", "Ждёт приёмки")
    assert (pending["settlement_label"], pending["amounts"], pending["lines"]) == (None, {}, [])
    item = goods_return.items.get()
    assert pending["items"] == [
        {"id": item.pk, "product_label": flour.plain_label, "bags": 4, "accepted_bags": None},
    ]
    assert (pending["accepted_by_name"], pending["accepted_at"]) == (None, None)

    storekeeper = user_with_perms("returns-storekeeper", codes=["storekeeper.confirm"])
    confirm_goods_return_item(goods_return, item.pk, 3)
    close_goods_return(goods_return, storekeeper)

    [row] = _get(api_as, viewer)
    assert (row["status"], row["status_label"], row["accepted_by_name"]) == ("partial", "Частично возвращено", "A B")
    assert row["accepted_at"] is not None
    assert (row["settlement_label"], row["amounts"], row["lines"]) == (None, {}, [])
    assert [(entry["bags"], entry["accepted_bags"]) for entry in row["items"]] == [(4, 3)]


def test_new_and_legacy_returns_side_by_side(api_as, viewer, returns_manager, departments):
    client = _client(departments[0])
    order = _shipped(client, price="1000")
    now = timezone.now()
    old_cancelled = GoodsReturn.objects.create(  # старый «из кассы», отменён до приёмки — денег не двигал
        client=client, settlement="cash", warehouse=get_default_warehouse(), status="cancelled",
    )
    GoodsReturn.objects.filter(pk=old_cancelled.pk).update(created_at=now - timedelta(days=2))
    legacy = _return(client, [(order, 2)], at=now - timedelta(days=1))
    new = _pending(client, returns_manager)

    rows = _get(api_as, viewer)

    assert _ids(rows) == [new.pk, legacy.pk, old_cancelled.pk]
    assert (rows[0]["settlement_label"], rows[0]["amounts"], rows[0]["lines"]) == (None, {}, [])
    assert (rows[1]["settlement_label"], rows[1]["amounts"]) == ("В счёт долга", {"KZT": "2000.00"})
    assert [(line["order"], line["bags"]) for line in rows[1]["lines"]] == [(order.pk, 2)]
    assert (rows[2]["status"], rows[2]["settlement_label"], rows[2]["amounts"]) == ("cancelled", None, {})


def test_new_returns_follow_the_client_department(api_as, user_with_perms, departments):
    mill, city = departments
    mill_client = _client(mill, "Мельничный")
    city_client = _client(city, "Городской")
    own = _pending(mill_client, user_with_perms("mill-editor", codes=["orders.edit"], department=mill))
    other = _pending(city_client, user_with_perms("city-editor", codes=["orders.edit"], department=city))
    keeper = user_with_perms("all-keeper", codes=["storekeeper.confirm"])
    confirm_goods_return_item(other, other.items.get().pk, 4)
    close_goods_return(other, keeper)  # закрытый — по-прежнему по отделу клиента
    pinned = user_with_perms("mill-returns", codes=["orders.view"], department=mill)
    everyone = user_with_perms("all-returns", codes=["orders.view"])

    assert _ids(_get(api_as, pinned)) == [own.pk]
    assert _ids(_get(api_as, everyone, department="city")) == [other.pk]
    assert _ids(_get(api_as, everyone, department="mill")) == [own.pk]
    assert _get(api_as, everyone, department="__unassigned") == []


def test_new_return_is_found_by_client_but_not_by_order_number(api_as, viewer, returns_manager, departments):
    client = _client(departments[0], "Береке")
    order = _shipped(client)
    goods_return = _pending(client, returns_manager)

    assert _ids(_get(api_as, viewer, search="Берек")) == [goods_return.pk]
    assert _get(api_as, viewer, search=str(order.pk)) == []


def test_closed_return_with_every_order_purged_stays_hidden(api_as, viewer, departments):
    client = _client(departments[0])
    order = _shipped(client)
    goods_return = _return(client, [(order, 2)])
    GoodsReturnLine.objects.filter(goods_return=goods_return).delete()  # окончательная очистка корзины

    assert _get(api_as, viewer) == []


def test_manager_cancels_a_pending_return_from_the_list(api_as, returns_manager, user_with_perms, departments):
    client = _client(departments[0])
    goods_return = _pending(client, returns_manager)
    url = f"{URL}{goods_return.pk}/cancel/"
    viewer_only = user_with_perms("returns-viewer-only", codes=["orders.view"], department=departments[0])
    stranger = user_with_perms("city-returns-editor", codes=["orders.view", "orders.edit"], department=departments[1])

    assert api_as(viewer_only).post(url).status_code == 403
    assert api_as(stranger).post(url).status_code == 404
    response = api_as(returns_manager).post(url)

    assert response.status_code == 200, response.data
    assert (response.data["id"], response.data["status"], response.data["status_label"]) == (
        goods_return.pk, "cancelled", "Отменён",
    )
    assert response.data["accepted_by_name"] == "A B"
    again = api_as(returns_manager).post(url)
    assert again.status_code == 400
    assert again.data["code"] == "goods_return_not_pending"
    assert EventLog.objects.filter(event_type="goods_return_status", payload__status="cancelled").count() == 1
    assert _ids(_get(api_as, returns_manager)) == [goods_return.pk]  # отменённый виден в списке


def test_query_count_does_not_grow_with_returns(count_queries, viewer, departments, make_user):
    mill, city = departments

    def add_return(n):
        client = _client(mill if n % 2 else city, f"Клиент {n}")
        flour = _flour(f"Мука {n} 50кг")
        tenge = _shipped(client, product=flour)
        dollars = _shipped(client, currency="USD", department="", product=_flour(f"Мука {n}б 50кг"))
        author = make_user(f"author-{n}")
        done = _return(client, [(tenge, 2), (dollars, 1)], user=author)
        GoodsReturn.objects.filter(pk=done.pk).update(accepted_by=author, accepted_at=timezone.now())
        GoodsReturnItem.objects.create(
            goods_return=done, product=flour, product_label_snapshot="x", bags=3, accepted_bags=3,
        )
        new = GoodsReturn.objects.create(client=client, warehouse=get_default_warehouse(), created_by=author)
        GoodsReturnItem.objects.create(goods_return=new, product=flour, product_label_snapshot="x", bags=5)

    add_return(0)
    few = count_queries(viewer, f"{URL}?page=1")
    for n in range(1, 6):
        add_return(n)
    many = count_queries(viewer, f"{URL}?page=1")

    assert few == many
