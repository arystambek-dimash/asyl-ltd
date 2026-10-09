"""«Возврат» по клиенту: менеджер создаёт заявку, кладовщик принимает мешки — только на склад.

Возврат не связан с заказами: мука — любая из каталога, при закрытии принятые
мешки приходят на склад возврата, долг, оплаты и касса не меняются. Возвраты,
закрытые по старым правилам (с деньгами), остаются в истории как были.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.goods_returns import (
    cancel_goods_return,
    close_goods_return,
    confirm_goods_return_item,
    create_goods_return,
    reopen_goods_return,
)
from apps.orders.models import GoodsReturn, GoodsReturnItem, GoodsReturnLine, Order, OrderItem, Payment, PaymentRefund
from apps.orders.querysets import order_remaining_by_id, with_order_amounts
from apps.orders.refunds import create_cash_refund
from apps.orders.services import record_staff_payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem, StockMovement, Warehouse
from apps.warehouse.services import get_default_warehouse, return_stock

pytestmark = pytest.mark.django_db

# Денежных полей в событиях возврата больше нет.
MONEY_KEYS = {"settlement", "amount", "amounts", "lines", "refund_ids", "cancelled_refund_ids"}


@pytest.fixture
def manager(user_with_perms, departments):
    return user_with_perms("mill-manager", codes=["orders.edit"], department=departments[0])


@pytest.fixture
def storekeeper(user_with_perms):
    return user_with_perms("storekeeper", codes=["storekeeper.view", "storekeeper.confirm"])


def _client(department, *, name="Клиент", phone="+7 (705) 565-65-65"):
    return Client.objects.create_with_user(first_name=name, phone=phone, department=department)


def _flour(name="Первый сорт DIKHAN 50кг", color="Blue"):
    product, _ = Product.objects.get_or_create(name=name, color=color, weight_kg=Decimal("50"))
    return product


def _shipped(client, items, *, currency="KZT", shipped=None):
    """Отгруженный заказ: ``items`` — [(товар, мешков, цена)]."""
    order = Order.objects.create(
        client=client, status="shipped", currency=currency, department=client.department.code,
    )
    for product, quantity, price in items:
        OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price=Decimal(price))
    Shipment.objects.create(order=order, shipped_at=shipped or timezone.now())
    return order


def _create(client, user, lines, *, warehouse=None):
    """Менеджер: «Создать возврат» — ``lines`` — [(товар, мешков)]; возврат ждёт приёмки."""
    return create_goods_return(
        client, user, warehouse=warehouse, lines=[{"product": product.pk, "bags": bags} for product, bags in lines],
    )


def _accept(goods_return, storekeeper, counts=None):
    """Кладовщик подтверждает каждую муку (``counts`` — {товар: принято}, иначе всё) и закрывает."""
    for item in goods_return.items.all():
        confirm_goods_return_item(goods_return, item.pk, item.bags if counts is None else counts[item.product_id])
    return close_goods_return(goods_return, storekeeper)


def _closed(client, manager, storekeeper, lines, *, counts=None, **options):
    return _accept(_create(client, manager, lines, **options), storekeeper, counts)


def _stock(product, warehouse=None):
    return StockItem.objects.get(product=product, warehouse=warehouse or get_default_warehouse()).bags


def _money(client):
    """Всё денежное у клиента и его заказов: возврат не меняет ничего из этого."""
    orders = Order.objects.filter(client=client).prefetch_related("items", "payments").order_by("id")
    payments = Payment.objects.filter(order__client=client).order_by("id")
    return {
        "debt": order_remaining_by_id(Order.objects.filter(client=client)),
        "orders": [(order.payment_status, order.total_amount, order.paid_total) for order in orders],
        "returned": list(
            OrderItem.objects.filter(order__client=client).order_by("id").values_list("returned_quantity", flat=True),
        ),
        "payments": list(payments.values_list("amount", "refunded_amount", "pending_refund_amount", "status")),
        "refunds": list(PaymentRefund.objects.order_by("id").values_list("amount", "status")),
        "lines": GoodsReturnLine.objects.count(),
        "order_events": EventLog.objects.filter(event_type="goods_return").count(),
    }


def _legacy(client, order, bags, *, settlement="debt"):
    """Возврат, закрытый по старым правилам: мешки легли на позицию заказа, долг меньше, склад пополнен."""
    item = order.items.get()
    now = timezone.now()
    goods_return = GoodsReturn.objects.create(
        client=client, settlement=settlement, warehouse=get_default_warehouse(), status="full",
        accepted_at=now, closed_by_storekeeper=True,
    )
    GoodsReturnItem.objects.create(
        goods_return=goods_return, product=item.product, product_label_snapshot=item.product.plain_label,
        bags=bags, accepted_bags=bags, checked_at=now,
    )
    GoodsReturnLine.objects.create(
        goods_return=goods_return, order_item=item, bags=bags, unit_price=item.unit_price,
        amount=item.unit_price * bags,
    )
    OrderItem.objects.filter(pk=item.pk).update(returned_quantity=F("returned_quantity") + bags)
    return_stock(item.product, bags, None, get_default_warehouse(), note=f"Возврат товара №{goods_return.pk}")
    return goods_return


def _status_events():
    return list(EventLog.objects.filter(event_type="goods_return_status").order_by("id"))


# Создание: заявка с любой мукой каталога, ничего не меняет.


def test_return_of_any_flour_waits_for_the_storekeeper_and_changes_nothing(manager, departments):
    client = _client(departments[0])
    never_bought = _flour("Высший сорт 50кг", "Red")  # у клиента нет ни одного заказа
    before = _money(client)

    goods_return = _create(client, manager, [(never_bought, 4)])

    goods_return.refresh_from_db()
    assert (goods_return.status, goods_return.created_by, goods_return.settlement) == ("pending", manager, "")
    assert goods_return.warehouse == get_default_warehouse()
    assert list(goods_return.items.values_list("product_id", "product_label_snapshot", "bags", "accepted_bags")) == [
        (never_bought.pk, never_bought.plain_label, 4, None),
    ]
    assert not StockMovement.objects.exists()
    assert _money(client) == before
    [event] = _status_events()
    assert (event.user, event.order, event.payload["client_id"], event.payload["status"]) == (
        manager, None, client.pk, "pending",
    )
    assert event.message == (
        f"Возврат товара №{goods_return.pk} клиента «{client.display_name}» создан: 4 мешка"
        f" — ждёт приёмки на складе «{get_default_warehouse().name}»"
    )
    assert MONEY_KEYS.isdisjoint(event.payload)


def test_rows_of_the_same_flour_add_up(manager, departments):
    client = _client(departments[0])
    flour = _flour()

    goods_return = _create(client, manager, [(flour, 4), (flour, 5)])

    assert list(goods_return.items.values_list("product_id", "bags")) == [(flour.pk, 9)]


def test_archived_flour_is_refused(manager, departments):
    client = _client(departments[0])
    archived = _flour("Снятая мука 50кг", "Red")
    Product.objects.filter(pk=archived.pk).update(is_active=False)

    with pytest.raises(ValidationError) as error:
        _create(client, manager, [(_flour(), 1), (archived, 2)])

    assert error.value.detail["code"] == "product_archived"
    assert str(error.value.detail["detail"]) == f"«{archived.plain_label}» в архиве — выберите другой товар"
    assert not GoodsReturn.objects.exists()


def test_unknown_flour_is_refused(manager, departments):
    with pytest.raises(ValidationError) as error:
        create_goods_return(
            _client(departments[0]), manager, warehouse=None, lines=[{"product": 999_999, "bags": 1}],
        )

    assert error.value.detail["code"] == "product_not_found"


@pytest.mark.parametrize("lines", [
    [], None, "x", [{"product": 1, "bags": 0}], [{"product": 1, "bags": "3"}], [{"product": 1, "bags": True}],
    [{"product": 1, "bags": 2_147_483_647}, {"product": 1, "bags": 1}],
])
def test_lines_must_be_positive_whole_bags(manager, departments, lines):
    with pytest.raises(ValidationError):
        create_goods_return(_client(departments[0]), manager, warehouse=None, lines=lines)

    assert not GoodsReturn.objects.exists()


def test_inactive_warehouse_is_refused(manager, departments):
    closed = Warehouse.objects.create(code="old", name="Старый склад", is_active=False)

    with pytest.raises(ValidationError) as error:
        _create(_client(departments[0]), manager, [(_flour(), 1)], warehouse=closed.pk)

    assert error.value.detail["code"] == "warehouse_inactive"
    assert not GoodsReturn.objects.exists()


def test_other_department_client_is_out_of_reach(auth_client, user_with_perms, departments):
    client = _client(departments[0])
    flour = _flour()
    stranger = user_with_perms("city-manager", codes=["orders.edit"], department=departments[1])
    body = {"warehouse": None, "lines": [{"product": flour.pk, "bags": 1}]}

    assert auth_client(stranger).post(f"/api/clients/{client.pk}/goods-return/", body, format="json").status_code == 404
    with pytest.raises(PermissionDenied):
        _create(client, stranger, [(flour, 1)])
    assert not GoodsReturn.objects.exists()


def test_api_creates_a_return_and_answers_with_its_row(auth_client, manager, departments):
    client = _client(departments[0], name="Дан Агро")
    flour = _flour()
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    api = auth_client(manager)

    response = api.post(
        f"/api/clients/{client.pk}/goods-return/",
        {"warehouse": second.pk, "lines": [{"product": flour.pk, "bags": 3}]},
        format="json",
    )

    assert response.status_code == 201, response.data
    goods_return = GoodsReturn.objects.get()
    item = goods_return.items.get()
    assert response.data["created_at"] == timezone.localtime(goods_return.created_at).isoformat()
    assert {key: value for key, value in response.data.items() if key != "created_at"} == {
        "id": goods_return.pk,
        "client_name": "Дан Агро",
        "warehouse_name": "Мельница 2",
        "created_by_name": "A B",
        "status": "pending",
        "status_label": "Ждёт приёмки",
        "accepted_by_name": None,
        "accepted_at": None,
        "items": [{"id": item.pk, "product_label": flour.plain_label, "bags": 3, "accepted_bags": None}],
        "settlement_label": None,
        "amounts": {},
        "lines": [],
    }
    assert api.get(f"/api/clients/{client.pk}/goods-return/").status_code == 405


def test_api_needs_orders_edit(auth_client, user_with_perms, departments):
    client = _client(departments[0])
    viewer = user_with_perms("viewer", codes=["clients.view", "orders.view"], department=departments[0])
    body = {"lines": [{"product": _flour().pk, "bags": 1}]}

    assert auth_client(viewer).post(f"/api/clients/{client.pk}/goods-return/", body, format="json").status_code == 403
    assert not GoodsReturn.objects.exists()


# Приёмка и закрытие: принятые мешки — на склад, деньги не меняются.


def test_close_puts_accepted_bags_on_the_chosen_warehouse_and_moves_no_money(
    manager, storekeeper, departments, boss,
):
    client = _client(departments[0])
    flour = _flour()
    now = timezone.now()
    paid = _shipped(client, [(flour, 10, "1000")], shipped=now)
    payment = record_staff_payment(paid, Decimal("10000"), boss, method="cash")
    create_cash_refund(payment, boss, amount="500", reason="Сдача клиенту")
    partly = _shipped(client, [(flour, 10, "1000")], shipped=now - timedelta(days=1))
    record_staff_payment(partly, Decimal("4000"), boss, method="cash")
    _shipped(client, [(flour, 20, "1000")], shipped=now - timedelta(days=2))  # весь в долге
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    goods_return = _create(client, manager, [(flour, 12)], warehouse=second.pk)
    before = _money(client)

    closed = _accept(goods_return, storekeeper)

    assert (closed.status, closed.accepted_by, closed.closed_by_storekeeper) == ("full", storekeeper, True)
    assert closed.accepted_at is not None
    assert _money(client) == before
    assert _stock(flour, second) == 12
    assert not StockItem.objects.filter(product=flour, warehouse=get_default_warehouse()).exists()
    movement = StockMovement.objects.get()
    assert (movement.reason, movement.delta, movement.created_by, movement.warehouse) == (
        "client_return", 12, storekeeper, second,
    )
    assert movement.note == f"Возврат товара №{closed.pk}, клиент «{client.display_name}»"
    event = _status_events()[-1]
    assert (event.user, event.payload["status"], event.payload["bags"], event.payload["accepted_bags"]) == (
        storekeeper, "full", 12, 12,
    )
    assert "принято 12 из 12 мешков — «Полностью возвращено»" in event.message
    assert MONEY_KEYS.isdisjoint(event.payload)


def test_partial_acceptance_stocks_only_accepted_bags(manager, storekeeper, departments):
    client = _client(departments[0])
    dikhan = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")
    _shipped(client, [(dikhan, 10, "1000")])
    before = _money(client)

    closed = _closed(client, manager, storekeeper, [(dikhan, 6), (korol, 4)], counts={dikhan.pk: 5, korol.pk: 4})

    assert (closed.status, closed.get_status_display()) == ("partial", "Частично возвращено")
    assert (_stock(dikhan), _stock(korol)) == (5, 4)
    assert _money(client) == before


def test_accepting_nothing_cancels_without_any_effect(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()

    closed = _closed(client, manager, storekeeper, [(flour, 4)], counts={flour.pk: 0})

    assert (closed.status, closed.accepted_by, closed.closed_by_storekeeper) == ("cancelled", storekeeper, True)
    assert not StockMovement.objects.exists()


def test_confirmed_items_change_nothing_until_close(manager, departments):
    goods_return = _create(_client(departments[0]), manager, [(_flour(), 4)])
    item = goods_return.items.get()

    confirm_goods_return_item(goods_return, item.pk, 4)
    confirm_goods_return_item(goods_return, item.pk, 3)  # пересчитал — до закрытия можно поменять

    item.refresh_from_db()
    assert item.accepted_bags == 3 and item.checked_at is not None
    assert not StockMovement.objects.exists()


def test_close_needs_every_flour_checked(manager, storekeeper, departments):
    dikhan = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")
    goods_return = _create(_client(departments[0]), manager, [(dikhan, 1), (korol, 1)])
    confirm_goods_return_item(goods_return, goods_return.items.get(product=dikhan).pk, 1)

    with pytest.raises(ValidationError) as error:
        close_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "items_unchecked"
    goods_return.refresh_from_db()
    assert goods_return.status == "pending"
    assert not StockMovement.objects.exists()


def test_closed_return_cannot_be_closed_confirmed_or_cancelled_again(manager, storekeeper, departments):
    flour = _flour()
    goods_return = _closed(_client(departments[0]), manager, storekeeper, [(flour, 4)])

    with pytest.raises(ValidationError) as again:
        close_goods_return(goods_return, storekeeper)
    with pytest.raises(ValidationError) as recount:
        confirm_goods_return_item(goods_return, goods_return.items.get().pk, 1)
    with pytest.raises(ValidationError) as cancel:
        cancel_goods_return(goods_return, manager)

    assert {again.value.detail["code"], recount.value.detail["code"], cancel.value.detail["code"]} == {
        "goods_return_not_pending",
    }
    assert _stock(flour) == 4


@pytest.mark.parametrize("count", [5, -1, True, "3", None, 2.0])
def test_accepted_count_is_whole_bags_up_to_requested(manager, departments, count):
    goods_return = _create(_client(departments[0]), manager, [(_flour(), 4)])

    with pytest.raises(ValidationError) as error:
        confirm_goods_return_item(goods_return, goods_return.items.get().pk, count)

    assert error.value.detail["code"] == "goods_return_bad_count"
    assert goods_return.items.get().accepted_bags is None


def test_unknown_item_is_not_found(manager, departments):
    goods_return = _create(_client(departments[0]), manager, [(_flour(), 4)])

    with pytest.raises(NotFound):
        confirm_goods_return_item(goods_return, goods_return.items.get().pk + 100, 1)


def test_flour_archived_after_creation_still_goes_on_stock(manager, storekeeper, departments):
    flour = _flour()
    goods_return = _create(_client(departments[0]), manager, [(flour, 4)])
    Product.objects.filter(pk=flour.pk).update(is_active=False)

    assert _accept(goods_return, storekeeper).status == "full"
    assert _stock(flour) == 4


def test_close_refuses_a_return_whose_flour_was_deleted(manager, storekeeper, departments):
    flour = _flour()
    goods_return = _create(_client(departments[0]), manager, [(flour, 4)])
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    goods_return.items.update(product=None)  # товар удалён физически: связь обнулилась, снимок остался

    with pytest.raises(ValidationError) as error:
        close_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "goods_return_product_deleted"
    assert f"«{flour.plain_label}» удалена из каталога" in str(error.value.detail["detail"])
    goods_return.refresh_from_db()
    assert goods_return.status == "pending"
    assert not StockMovement.objects.exists()


def test_close_is_out_of_reach_for_another_department(manager, user_with_perms, departments):
    goods_return = _create(_client(departments[0]), manager, [(_flour(), 4)])
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    stranger = user_with_perms("city-keeper", codes=["storekeeper.confirm"], department=departments[1])

    with pytest.raises(PermissionDenied):
        close_goods_return(goods_return, stranger)

    goods_return.refresh_from_db()
    assert goods_return.status == "pending"
    assert not StockMovement.objects.exists()


def test_manager_cancels_a_pending_return(manager, storekeeper, departments):
    goods_return = _create(_client(departments[0]), manager, [(_flour(), 4)])

    cancel_goods_return(goods_return, manager)

    goods_return.refresh_from_db()
    assert (goods_return.status, goods_return.accepted_by, goods_return.closed_by_storekeeper) == (
        "cancelled", manager, False,
    )
    with pytest.raises(ValidationError) as close:
        close_goods_return(goods_return, storekeeper)
    assert close.value.detail["code"] == "goods_return_not_pending"
    assert not StockMovement.objects.exists()
    event = _status_events()[-1]
    assert (event.user, event.payload["status"]) == (manager, "cancelled")
    assert MONEY_KEYS.isdisjoint(event.payload)


def test_a_return_created_by_the_old_rules_closes_without_money(manager, storekeeper, departments):
    """Создан до перемены правил «в счёт долга» и ждал приёмки: закрывается уже без денег."""
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = _create(client, manager, [(flour, 4)])
    GoodsReturn.objects.filter(pk=goods_return.pk).update(settlement="debt")
    before = _money(client)

    closed = _accept(goods_return, storekeeper)

    closed.refresh_from_db()
    assert (closed.status, closed.settlement) == ("full", "")
    assert _money(client) == before
    assert _stock(flour) == 4
    assert reopen_goods_return(closed, storekeeper).status == "pending"  # это уже новый возврат


def test_old_image_insert_without_status_counts_as_fully_returned(departments):
    """Откат образа до приёмки проводит возврат сразу и не знает колонки статуса: «Полностью возвращено»."""
    client = _client(departments[0])
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO orders_goodsreturn (client_id, settlement, warehouse_id, created_at)"
            " VALUES (%s, 'debt', %s, now()) RETURNING id",
            [client.pk, get_default_warehouse().pk],
        )
        [pk] = cursor.fetchone()

    assert GoodsReturn.objects.get(pk=pk).status == "full"


# «Исправить»: закрытый возврат снова ждёт приёмки, принятые мешки уходят со склада.


def test_reopen_takes_the_bags_back_and_waits_with_the_previous_counts(manager, storekeeper, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    record_staff_payment(order, Decimal("3000"), boss, method="cash")
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    closed = _closed(client, manager, storekeeper, [(flour, 4)], warehouse=second.pk)
    checked_at = closed.items.get().checked_at
    before = _money(client)

    reopened = reopen_goods_return(closed, storekeeper)

    reopened.refresh_from_db()
    assert (reopened.status, reopened.accepted_by, reopened.accepted_at) == ("pending", None, None)
    assert reopened.closed_by_storekeeper is False
    item = reopened.items.get()
    assert (item.accepted_bags, item.checked_at) == (4, checked_at)  # прежние числа — уже проверены
    assert _stock(flour, second) == 0
    undo = StockMovement.objects.get(reason="client_return_undo")
    assert (undo.warehouse, undo.delta, undo.created_by) == (second, -4, storekeeper)
    assert undo.note == f"Исправление возврата товара №{closed.pk}, клиент «{client.display_name}»"
    assert _money(client) == before
    status = _status_events()[-1]
    assert (status.user, status.payload["action"], status.payload["status"]) == (storekeeper, "reopen", "pending")
    assert status.payload["before"]["status"] == "full"
    assert status.payload["before"]["accepted_by"] == "A B"
    assert status.payload["after"] == {"status": "pending"}
    assert [(row["bags"], row["accepted_bags"]) for row in status.payload["items"]] == [(4, 4)]
    assert "был «Полностью возвращено», принято 4 из 4 мешков" in status.message
    assert MONEY_KEYS.isdisjoint(status.payload)


def test_reopen_change_and_close_ends_like_one_close_with_the_new_counts(manager, storekeeper, departments):
    once_flour, once_other = _flour("Мука А 50кг"), _flour("Мука Б 50кг", "Green")
    fixed_flour, fixed_other = _flour("Мука В 50кг"), _flour("Мука Г 50кг", "Green")
    client = _client(departments[0])

    _closed(client, manager, storekeeper, [(once_flour, 8), (once_other, 4)],
            counts={once_flour.pk: 6, once_other.pk: 3})
    mistaken = _closed(client, manager, storekeeper, [(fixed_flour, 8), (fixed_other, 4)])  # «принято всё» — ошибка
    reopened = reopen_goods_return(mistaken, storekeeper)
    for item in reopened.items.all():
        confirm_goods_return_item(reopened, item.pk, {fixed_flour.pk: 6, fixed_other.pk: 3}[item.product_id])
    corrected = close_goods_return(reopened, storekeeper)

    assert corrected.status == "partial"
    assert [_stock(product) for product in (once_flour, once_other)] == [6, 3]
    assert [_stock(product) for product in (fixed_flour, fixed_other)] == [6, 3]


def test_reopen_of_a_return_closed_with_nothing_accepted(manager, storekeeper, departments):
    flour = _flour()
    closed = _closed(_client(departments[0]), manager, storekeeper, [(flour, 4)], counts={flour.pk: 0})
    assert closed.status == "cancelled"

    reopened = reopen_goods_return(closed, storekeeper)

    assert (reopened.status, reopened.items.get().accepted_bags) == ("pending", 0)
    assert not StockMovement.objects.exists()  # на склад ничего не клали — забирать нечего
    confirm_goods_return_item(reopened, reopened.items.get().pk, 2)
    assert close_goods_return(reopened, storekeeper).status == "partial"
    assert _stock(flour) == 2


def test_reopen_of_the_storekeepers_own_cancel_changes_nothing(manager, storekeeper, departments):
    """Кладовщик отменил возврат, когда муку уже посчитал: на склад не клали — забирать нечего."""
    goods_return = _create(_client(departments[0]), manager, [(_flour(), 4)])
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    cancel_goods_return(goods_return, storekeeper, by_storekeeper=True)

    reopened = reopen_goods_return(goods_return, storekeeper)

    assert (reopened.status, reopened.items.get().accepted_bags, reopened.closed_by_storekeeper) == ("pending", 4, False)
    assert not StockMovement.objects.exists()


@pytest.mark.parametrize("closed_first", [False, True])
def test_reopen_of_a_managers_cancel_is_refused(manager, storekeeper, departments, closed_first):
    """Менеджер решил, что возврата не будет: кладовщик не вернёт его на приёмку."""
    flour = _flour()
    goods_return = _create(_client(departments[0]), manager, [(flour, 4)])
    if closed_first:  # кладовщик закрыл и исправил, менеджер отменил исправленный
        reopen_goods_return(_accept(goods_return, storekeeper), storekeeper)
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    cancel_goods_return(goods_return, manager)

    with pytest.raises(ValidationError) as error:
        reopen_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "goods_return_cannot_reopen"
    assert "его отменил менеджер" in str(error.value.detail["detail"])
    goods_return.refresh_from_db()
    assert (goods_return.status, goods_return.accepted_by) == ("cancelled", manager)
    # Склад — как до возврата: мешки, принятые первым закрытием, исправление уже забрало.
    assert sum(StockItem.objects.filter(product=flour).values_list("bags", flat=True)) == 0


def test_reopen_twice_or_of_a_pending_return_is_refused(manager, storekeeper, departments):
    flour = _flour()
    closed = _closed(_client(departments[0]), manager, storekeeper, [(flour, 4)])
    reopen_goods_return(closed, storekeeper)

    with pytest.raises(ValidationError) as twice:
        reopen_goods_return(closed, storekeeper)

    assert twice.value.detail["code"] == "goods_return_not_closed"
    assert _stock(flour) == 0
    assert StockMovement.objects.filter(reason="client_return_undo").count() == 1


def test_reopen_takes_the_bags_back_even_after_they_were_shipped(manager, storekeeper, departments):
    from apps.warehouse.services import deduct_stock

    flour = _flour()
    closed = _closed(_client(departments[0]), manager, storekeeper, [(flour, 4)])
    deduct_stock(flour, 3, storekeeper)  # три принятых мешка уже уехали

    reopen_goods_return(closed, storekeeper)

    assert _stock(flour) == -3
    negative = EventLog.objects.get(event_type="stock_negative")
    assert (negative.payload["had"], negative.payload["deduct"]) == (1, 4)


def test_reopen_refuses_a_return_whose_flour_was_deleted(manager, storekeeper, departments):
    flour = _flour()
    closed = _closed(_client(departments[0]), manager, storekeeper, [(flour, 4)])
    closed.items.update(product=None)

    with pytest.raises(ValidationError) as error:
        reopen_goods_return(closed, storekeeper)

    assert error.value.detail["code"] == "goods_return_cannot_reopen"
    assert "мука удалена из каталога" in str(error.value.detail["detail"])
    closed.refresh_from_db()
    assert closed.status == "full"
    assert _stock(flour) == 4


def test_reopen_is_out_of_reach_for_another_department(manager, storekeeper, user_with_perms, departments):
    closed = _closed(_client(departments[0]), manager, storekeeper, [(_flour(), 4)])
    stranger = user_with_perms("city-keeper", codes=["storekeeper.confirm"], department=departments[1])

    with pytest.raises(PermissionDenied):
        reopen_goods_return(closed, stranger)

    closed.refresh_from_db()
    assert closed.status == "full"


# Старые возвраты (с деньгами): остаются как были, не исправляются.


@pytest.mark.parametrize("settlement", ["debt", "cash"])
def test_reopen_of_a_legacy_return_is_refused_and_changes_nothing(storekeeper, departments, settlement):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    legacy = _legacy(client, order, 3, settlement=settlement)
    before = _money(client)

    with pytest.raises(ValidationError) as error:
        reopen_goods_return(legacy, storekeeper)

    assert error.value.detail["code"] == "goods_return_legacy"
    assert str(error.value.detail["detail"]) == (
        "Возврат по старым правилам (с деньгами) — исправить нельзя, обратитесь к руководителю"
    )
    legacy.refresh_from_db()
    assert (legacy.status, legacy.settlement, legacy.lines.count()) == ("full", settlement, 1)
    assert OrderItem.objects.get(order=order).returned_quantity == 3
    assert _money(client) == before
    assert _stock(flour) == 3
    assert not StockMovement.objects.filter(reason="client_return_undo").exists()


def test_an_old_return_the_storekeeper_cancelled_is_fixed_by_the_new_rules(manager, storekeeper, departments):
    """Старый «в счёт долга», который кладовщик отменил (ничего не принял): денег не двигал — исправляется как новый."""
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    cancelled = _closed(client, manager, storekeeper, [(flour, 4)], counts={flour.pk: 0})
    GoodsReturn.objects.filter(pk=cancelled.pk).update(settlement="debt")
    before = _money(client)

    reopened = reopen_goods_return(cancelled, storekeeper)
    confirm_goods_return_item(reopened, reopened.items.get().pk, 4)
    closed = close_goods_return(reopened, storekeeper)

    closed.refresh_from_db()
    assert (closed.status, closed.settlement) == ("full", "")
    assert _stock(flour) == 4
    assert _money(client) == before


def test_order_amount_counts_only_bags_kept_by_the_client(departments):
    order = _shipped(_client(departments[0]), [(_flour(), 100, "3000")])
    OrderItem.objects.filter(order=order).update(returned_quantity=30)

    order = Order.objects.prefetch_related("items", "payments").get(pk=order.pk)
    item = order.items.get()
    assert (item.quantity, item.returned_quantity, item.sold_quantity) == (100, 30, 70)
    assert order.ordered_bags == 100  # отгружено — физика, не меняется
    assert order.total_amount == Decimal("210000")
    assert with_order_amounts(Order.objects.filter(pk=order.pk)).get().amount_total == Decimal("210000")
    assert order_remaining_by_id(Order.objects.filter(pk=order.pk)) == {order.pk: Decimal("210000")}


def test_order_with_a_legacy_return_cannot_be_rolled_back_or_recomposed(departments, boss):
    from apps.orders.services import replace_items
    from apps.shipments.services import rollback_shipment

    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    _legacy(client, order, 2)

    with pytest.raises(ValidationError) as rollback:
        rollback_shipment(order, boss, target_status="confirmed", reason="ошибка отгрузки")
    with pytest.raises(ValidationError) as edit:
        replace_items(order, [{"product": flour, "quantity": 5}], {flour.pk: Decimal("1000")}, boss,
                      edit_reason="исправление количества")

    assert rollback.value.detail["code"] == "order_has_returns"
    assert edit.value.detail["code"] == "order_has_returns"


def test_order_api_statement_and_portal_still_show_a_legacy_return(auth_client, client_user, departments, boss):
    from types import SimpleNamespace

    from apps.clients.reports.statements.presentation import operation_display

    client = Client.objects.create_with_user(
        first_name="Портал", phone="+7 (705) 111-11-11", department=departments[0], user=client_user,
    )
    order = _shipped(client, [(_flour(), 10, "1000")])
    _legacy(client, order, 3)

    item = auth_client(boss).get(f"/api/orders/{order.pk}/").data["items"][0]
    assert (item["quantity"], item["returned_quantity"]) == (10, 3)
    portal = auth_client(client_user).get(f"/api/portal/orders/{order.pk}/").data
    assert (portal["items"][0]["returned_quantity"], Decimal(portal["total_amount"])) == (3, Decimal("7000"))
    sale = SimpleNamespace(kind="sale", order=Order.objects.prefetch_related("items").get(pk=order.pk))
    assert operation_display(sale).description.endswith("× 10 (возврат 3)")


def test_migration_marks_returns_closed_by_the_storekeeper(manager, storekeeper, departments):
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("apps.orders.migrations.0055_goodsreturn_closed_by_storekeeper")
    client = _client(departments[0])
    flour = _flour()
    by_keeper = _closed(client, manager, storekeeper, [(flour, 2)], counts={flour.pk: 0})
    by_manager = _create(client, manager, [(flour, 2)])
    cancel_goods_return(by_manager, manager)
    GoodsReturn.objects.update(closed_by_storekeeper=False)  # как до миграции

    migration.mark_storekeeper_closed(django_apps, None)

    assert dict(GoodsReturn.objects.values_list("pk", "closed_by_storekeeper")) == {
        by_keeper.pk: True, by_manager.pk: False,
    }
