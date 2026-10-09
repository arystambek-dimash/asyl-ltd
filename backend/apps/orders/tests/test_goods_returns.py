"""«Возврат» по клиенту: менеджер создаёт, кладовщик принимает, принятые мешки раскладываются по заказам.

Раскладка — от новой отгрузки к старой. Долг, касса и склад меняются только при
закрытии возврата кладовщиком и только за принятые мешки.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.goods_returns import (
    cancel_goods_return,
    close_goods_return,
    confirm_goods_return_item,
    record_goods_return,
    reopen_goods_return,
)
from apps.orders.models import GoodsReturn, GoodsReturnLine, Order, OrderItem, Payment, PaymentRefund
from apps.orders.querysets import order_remaining_by_id, with_order_amounts
from apps.orders.services import record_staff_payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem, StockMovement, Warehouse
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(user_with_perms, departments):
    return user_with_perms(
        "mill-manager", codes=["orders.edit", "payments.confirm", "payments.create"], department=departments[0],
    )


@pytest.fixture
def storekeeper(user_with_perms):
    return user_with_perms("storekeeper", codes=["storekeeper.view", "storekeeper.confirm"])


def _client(department, *, currency="KZT"):
    return Client.objects.create_with_user(
        first_name="Клиент", phone="+7 (705) 565-65-65", department=department, currency=currency,
    )


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


def _create(client, user, lines, *, settlement="debt", preview=False, warehouse=None):
    """Менеджер: «Проверить» (``preview``) или «Создать возврат» — ждёт приёмки."""
    return record_goods_return(
        client, user, settlement=settlement, warehouse=warehouse, lines=lines, preview=preview,
    )


def _accept(return_id, storekeeper, counts=None):
    """Кладовщик подтверждает каждую муку (``counts`` — {товар: принято}, иначе всё) и закрывает."""
    goods_return = GoodsReturn.objects.get(pk=return_id)
    for item in goods_return.items.all():
        accepted = item.bags if counts is None else counts[item.product_id]
        confirm_goods_return_item(goods_return, item.pk, accepted)
    return close_goods_return(goods_return, storekeeper)


def _return(client, user, lines, *, storekeeper=None, **options):
    """Создать возврат и принять его целиком; ответ — раскладка создания."""
    plan = _create(client, user, lines, **options)
    _accept(plan["return_id"], storekeeper or user)
    return plan


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


def test_debt_return_goes_to_the_newest_shipment_first(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    now = timezone.now()
    old = _shipped(client, [(flour, 100, "3000")], shipped=now - timedelta(days=5))
    new = _shipped(client, [(flour, 20, "3200")], shipped=now - timedelta(days=1))

    plan = _return(client, manager, [{"product": flour.pk, "bags": 50}])

    assert [(row["order_id"], row["lines"][0]["bags"], row["amount"]) for row in plan["orders"]] == [
        (new.pk, 20, "64000.00"),
        (old.pk, 30, "90000.00"),
    ]
    assert (plan["bags"], plan["amounts"], plan["settlement"]) == (50, {"KZT": "154000.00"}, "debt")
    assert plan["orders"][0]["currency"] == "KZT"
    assert plan["orders"][0]["lines"][0]["label"] == "Первый сорт DIKHAN 50кг"
    assert OrderItem.objects.get(order=old).returned_quantity == 30
    assert OrderItem.objects.get(order=new).returned_quantity == 20
    old = Order.objects.prefetch_related("items", "payments").get(pk=old.pk)
    assert old.total_amount == Decimal("210000")  # 70 × 3000


def test_preview_writes_nothing(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "3000")])

    plan = _create(client, manager, [{"product": flour.pk, "bags": 4}], preview=True)

    assert plan["bags"] == 4 and "return_id" not in plan
    assert not GoodsReturn.objects.exists()
    assert OrderItem.objects.get().returned_quantity == 0
    assert not StockMovement.objects.filter(reason="client_return").exists()


def test_debt_mode_takes_only_what_the_order_still_owes(manager, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    now = timezone.now()
    paid = _shipped(client, [(flour, 10, "1000")], shipped=now)  # новая, но оплачена
    record_staff_payment(paid, Decimal("10000"), boss, method="cash")
    partly = _shipped(client, [(flour, 10, "1000")], shipped=now - timedelta(days=1))
    record_staff_payment(partly, Decimal("7500"), boss, method="cash")  # долг 2500 → 2 мешка

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": flour.pk, "bags": 3}])

    assert error.value.detail["code"] == "goods_return_exceeds"
    assert "Максимум 2 мешка" in str(error.value.detail["detail"])
    plan = _return(client, manager, [{"product": flour.pk, "bags": 2}])
    assert [row["order_id"] for row in plan["orders"]] == [partly.pk]


def test_cash_mode_refunds_paid_orders_from_the_till_at_close(manager, storekeeper, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    paid = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(paid, Decimal("10000"), boss, method="cash")
    _shipped(client, [(flour, 10, "1000")], shipped=timezone.now() - timedelta(days=1))  # в долге — не для кассы

    plan = _create(client, manager, [{"product": flour.pk, "bags": 3}], settlement="cash")
    assert not PaymentRefund.objects.exists()  # касса отдаёт деньги только при закрытии
    _accept(plan["return_id"], storekeeper)

    assert [row["order_id"] for row in plan["orders"]] == [paid.pk]
    refund = PaymentRefund.objects.get(payment=payment)
    assert (refund.amount, refund.method, refund.status) == (Decimal("3000.00"), "cash", "completed")
    assert refund.reason == f"Возврат товара №{plan['return_id']}"
    # Деньги отдаёт тот, кто создал возврат с правом кассы; журнал называет кладовщика.
    assert refund.requested_by == manager
    assert EventLog.objects.get(event_type="goods_return", order=paid).user == storekeeper
    paid.refresh_from_db()
    assert paid.payment_status == "settled"  # 7 мешков = 7000, оплачено 10000 − 3000


def test_cash_return_closes_after_the_client_moved_to_another_department(manager, storekeeper, departments, boss):
    """Отдел проверяют у кладовщика при закрытии; создавший — только автор возврата денег."""
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(order, Decimal("10000"), boss, method="cash")
    plan = _create(client, manager, [{"product": flour.pk, "bags": 2}], settlement="cash")
    Client.objects.filter(pk=client.pk).update(department=departments[1])

    closed = _accept(plan["return_id"], storekeeper)

    assert closed.status == "full"
    refund = PaymentRefund.objects.get(payment=payment)
    assert (refund.amount, refund.requested_by) == (Decimal("2000.00"), manager)
    assert OrderItem.objects.get(order=order).returned_quantity == 2


def test_cash_mode_needs_payments_confirm(user_with_perms, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    editor = user_with_perms("editor", codes=["orders.edit"], department=departments[0])

    with pytest.raises(PermissionDenied):
        _return(client, editor, [{"product": flour.pk, "bags": 1}], settlement="cash")


def test_rows_of_the_same_flour_add_up(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])

    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}, {"product": flour.pk, "bags": 5}])

    assert plan["bags"] == 9
    assert OrderItem.objects.get().returned_quantity == 9


def test_trash_and_unpriced_items_are_not_used(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    trashed = _shipped(client, [(flour, 10, "1000")])
    Order.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
    _shipped(client, [(flour, 10, "0")])
    unpriced = _shipped(client, [(flour, 10, "1000")])
    OrderItem.objects.filter(order=unpriced).update(unit_price=None)

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": flour.pk, "bags": 1}])

    assert error.value.detail["code"] == "goods_return_exceeds"


def test_return_puts_bags_on_the_chosen_warehouse_and_logs_each_order(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")

    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}], warehouse=second.pk, storekeeper=storekeeper)

    assert StockItem.objects.get(product=flour, warehouse=second).bags == 4
    movement = StockMovement.objects.get(product=flour, warehouse=second)
    assert (movement.reason, movement.created_by) == ("client_return", storekeeper)
    event = EventLog.objects.get(event_type="goods_return", order=order)
    assert event.payload["goods_return_id"] == plan["return_id"]
    assert event.payload["bags"] == 4


@pytest.mark.parametrize("lines", [[], None, [{"product": 1, "bags": 0}], [{"product": 1, "bags": "3"}]])
def test_lines_must_be_positive_whole_bags(manager, departments, lines):
    with pytest.raises(ValidationError):
        _return(_client(departments[0]), manager, lines)


def test_unknown_settlement_is_refused(manager, departments):
    with pytest.raises(ValidationError):
        _return(_client(departments[0]), manager, [{"product": _flour().pk, "bags": 1}], settlement="gift")


def test_api_previews_and_creates_a_return_waiting_for_the_storekeeper(auth_client, manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    body = {"settlement": "debt", "warehouse": None, "lines": [{"product": flour.pk, "bags": 3}]}
    api = auth_client(manager)

    preview = api.post(f"/api/clients/{client.pk}/goods-return/", {**body, "preview": True}, format="json")
    done = api.post(f"/api/clients/{client.pk}/goods-return/", {**body, "preview": False}, format="json")

    assert preview.status_code == 200, preview.data
    assert preview.data["bags"] == 3 and "return_id" not in preview.data
    assert done.status_code == 200, done.data
    assert done.data["return_id"] == GoodsReturn.objects.get().pk
    assert (done.data["status"], done.data["bags"], done.data["amounts"]) == ("pending", 3, {"KZT": "3000.00"})
    assert OrderItem.objects.get().returned_quantity == 0


def test_api_needs_orders_edit_and_boolean_preview(auth_client, user_with_perms, manager, departments):
    client = _client(departments[0])
    viewer = user_with_perms("viewer", codes=["clients.view"], department=departments[0])
    body = {"settlement": "debt", "lines": [{"product": _flour().pk, "bags": 1}]}

    assert auth_client(viewer).post(f"/api/clients/{client.pk}/goods-return/", body, format="json").status_code == 403
    response = auth_client(manager).post(
        f"/api/clients/{client.pk}/goods-return/", {**body, "preview": "true"}, format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == "bad_preview"


def test_order_with_a_return_cannot_be_rolled_back_or_recomposed(manager, departments, boss):
    from apps.orders.services import replace_items
    from apps.shipments.services import rollback_shipment

    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    _return(client, manager, [{"product": flour.pk, "bags": 2}])

    with pytest.raises(ValidationError) as rollback:
        rollback_shipment(order, boss, target_status="confirmed", reason="ошибка отгрузки")
    with pytest.raises(ValidationError) as edit:
        replace_items(order, [{"product": flour, "quantity": 5}], {flour.pk: Decimal("1000")}, boss,
                      edit_reason="исправление количества")

    assert rollback.value.detail["code"] == "order_has_returns"
    assert edit.value.detail["code"] == "order_has_returns"


def test_order_api_and_statement_show_the_return(auth_client, manager, departments, boss):
    from types import SimpleNamespace

    from apps.clients.reports.statements.presentation import operation_display

    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    _return(client, manager, [{"product": flour.pk, "bags": 3}])

    item = auth_client(boss).get(f"/api/orders/{order.pk}/").data["items"][0]
    assert (item["quantity"], item["returned_quantity"]) == (10, 3)

    sale = SimpleNamespace(kind="sale", order=Order.objects.prefetch_related("items").get(pk=order.pk))
    assert operation_display(sale).description.endswith("× 10 (возврат 3)")


def test_second_return_takes_only_bags_still_with_the_client(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    _return(client, manager, [{"product": flour.pk, "bags": 7}])

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": flour.pk, "bags": 4}])
    _return(client, manager, [{"product": flour.pk, "bags": 3}])

    assert "Максимум 3 мешка" in str(error.value.detail["detail"])
    item = OrderItem.objects.get(order=order)
    assert (item.returned_quantity, item.sold_quantity) == (10, 0)
    order = Order.objects.prefetch_related("items", "payments").get(pk=order.pk)
    assert order.total_amount == Decimal("0")


def test_several_flours_in_one_return_share_each_order_budget(manager, departments, boss):
    client = _client(departments[0])
    dikhan = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")
    order = _shipped(client, [(dikhan, 10, "1000"), (korol, 10, "500")])
    record_staff_payment(order, Decimal("12000"), boss, method="cash")  # долг 3000

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": dikhan.pk, "bags": 2}, {"product": korol.pk, "bags": 3}])
    plan = _return(client, manager, [{"product": dikhan.pk, "bags": 2}, {"product": korol.pk, "bags": 2}])

    assert "Максимум 2 мешка «Второй сорт KOROL 50кг» в счёт долга" in str(error.value.detail["detail"])
    assert plan["amounts"] == {"KZT": "3000.00"}
    order.refresh_from_db()
    assert order.payment_status == "settled"  # 15000 − 3000 = 12000 = оплачено


def test_nothing_to_put_the_flour_on_is_said_plainly(manager, departments):
    client = _client(departments[0])
    other = _flour("Высший сорт 50кг", "Red")
    _shipped(client, [(_flour(), 10, "1000")])

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": other.pk, "bags": 1}], settlement="debt")

    assert str(error.value.detail["detail"]) == "«Высший сорт 50кг»: нет заказов в долге с этой мукой"


def test_currency_comes_from_each_order_and_totals_are_not_mixed(manager, departments, boss):
    client = _client(departments[0], currency="USD")
    flour = _flour()
    now = timezone.now()
    usd = _shipped(client, [(flour, 10, "20")], currency="USD", shipped=now)
    payment = record_staff_payment(usd, Decimal("200"), boss, method="cash")
    kzt = _shipped(client, [(flour, 10, "1000")], shipped=now - timedelta(days=1))
    record_staff_payment(kzt, Decimal("10000"), boss, method="cash")

    plan = _return(client, manager, [{"product": flour.pk, "bags": 12}], settlement="cash")

    assert [(row["order_id"], row["currency"], row["amount"]) for row in plan["orders"]] == [
        (usd.pk, "USD", "200.00"),
        (kzt.pk, "KZT", "2000.00"),
    ]
    assert plan["amounts"] == {"USD": "200.00", "KZT": "2000.00"}
    assert PaymentRefund.objects.get(payment=payment).amount == Decimal("200.00")
    event = EventLog.objects.get(event_type="goods_return", order=usd)
    assert event.message.endswith("200 $ из кассы")


def test_other_department_client_is_out_of_reach(auth_client, user_with_perms, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    stranger = user_with_perms("city-manager", codes=["orders.edit"], department=departments[1])
    body = {"settlement": "debt", "lines": [{"product": flour.pk, "bags": 1}], "preview": True}

    api = auth_client(stranger)
    assert api.post(f"/api/clients/{client.pk}/goods-return/", body, format="json").status_code == 404
    assert api.get(f"/api/clients/{client.pk}/goods-return/").status_code == 404
    with pytest.raises(PermissionDenied):
        _return(client, stranger, [{"product": flour.pk, "bags": 1}])
    assert not GoodsReturn.objects.exists()


def test_inactive_warehouse_is_refused(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    closed = Warehouse.objects.create(code="old", name="Старый склад", is_active=False)

    with pytest.raises(ValidationError) as error:
        _return(client, manager, [{"product": flour.pk, "bags": 1}], warehouse=closed.pk)

    assert error.value.detail["code"] == "warehouse_inactive"
    assert OrderItem.objects.get().returned_quantity == 0


def test_returnable_products_list_only_what_the_client_got(auth_client, manager, departments, boss):
    client = _client(departments[0])
    dikhan = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")
    _flour("Никогда не покупал 50кг", "Red")
    _shipped(client, [(dikhan, 10, "1000")])  # в долге
    paid = _shipped(client, [(korol, 4, "500")])
    record_staff_payment(paid, Decimal("2000"), boss, method="cash")
    _shipped(client, [(dikhan, 50, "5")], currency="USD")  # и долларовый заказ — валюта из заказа

    response = auth_client(manager).get(f"/api/clients/{client.pk}/goods-return/")

    assert response.status_code == 200, response.data
    assert response.data["products"] == [
        {"product": korol.pk, "label": "Второй сорт KOROL 50кг", "debt_bags": 0, "cash_bags": 4},
        {"product": dikhan.pk, "label": "Первый сорт DIKHAN 50кг", "debt_bags": 60, "cash_bags": 0},
    ]


def test_client_sees_the_return_in_the_portal(auth_client, client_user, manager, departments):
    client = Client.objects.create_with_user(
        first_name="Портал", phone="+7 (705) 111-11-11", department=departments[0], user=client_user,
    )
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    _return(client, manager, [{"product": flour.pk, "bags": 10}])

    data = auth_client(client_user).get(f"/api/portal/orders/{order.pk}/").data

    assert (data["items"][0]["quantity"], data["items"][0]["returned_quantity"]) == (10, 10)
    assert Decimal(data["total_amount"]) == Decimal("0")
    assert not Order.objects.prefetch_related("items", "payments").get(pk=order.pk).is_debt


def _with_bonus(client, bags=100, bonus=1, price="3000"):
    flour = _flour()
    order = _shipped(client, [(flour, bags, price)])
    OrderItem.objects.create(order=order, product=flour, quantity=bonus, is_bonus=True, unit_price=0)
    return order, flour


def test_a_full_return_takes_the_bonus_bag_back_at_no_money(departments, manager):
    from apps.orders.goods_returns import returnable_products

    client = _client(departments[0])
    order, flour = _with_bonus(client)

    assert [row["debt_bags"] for row in returnable_products(client)] == [101]
    result = _return(client, manager, [{"product": flour.pk, "bags": 101}])

    assert result["bags"] == 101
    assert result["amounts"] == {"KZT": "300000.00"}
    assert [line["label"] for line in result["orders"][0]["lines"]] == [
        flour.plain_label, f"{flour.plain_label} (бонус)",
    ]
    order.refresh_from_db()
    assert order.total_amount == 0
    assert sorted(order.items.values_list("is_bonus", "returned_quantity")) == [(False, 100), (True, 1)]


def test_close_refuses_to_move_shown_paid_bags_to_the_bonus_after_the_debt_was_paid(
    manager, storekeeper, departments, boss,
):
    """Менеджеру показали «долг −2 000 ₸»: клиент погасил долг — мешки не уходят в бонус без денег."""
    client = _client(departments[0])
    order, flour = _with_bonus(client, bags=10, bonus=2, price="1000")
    plan = _create(client, manager, [{"product": flour.pk, "bags": 2}])
    goods_return = GoodsReturn.objects.get(pk=plan["return_id"])
    assert plan["amounts"] == {"KZT": "2000.00"}
    assert list(goods_return.items.values_list("bags", "paid_bags")) == [(2, 2)]
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 2)
    record_staff_payment(order, Decimal("10000"), boss, method="cash")  # долга больше нет

    with pytest.raises(ValidationError) as error:
        close_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "goods_return_no_longer_fits"
    assert f"за «{flour.plain_label}» в счёт долга засчитывается 0 из 2 мешков" in str(error.value.detail["detail"])
    goods_return.refresh_from_db()
    assert goods_return.status == "pending"
    assert not goods_return.lines.exists()
    assert sorted(order.items.values_list("is_bonus", "returned_quantity")) == [(False, 0), (True, 0)]
    assert not StockMovement.objects.filter(reason="client_return").exists()


def test_bonus_bags_shown_free_still_close_after_fewer_were_accepted(manager, storekeeper, departments):
    client = _client(departments[0])
    order, flour = _with_bonus(client, bags=10, bonus=2, price="1000")
    plan = _create(client, manager, [{"product": flour.pk, "bags": 12}])
    assert GoodsReturn.objects.get(pk=plan["return_id"]).items.get().paid_bags == 10

    closed = _accept(plan["return_id"], storekeeper, {flour.pk: 11})

    assert closed.status == "partial"
    assert sorted(order.items.values_list("is_bonus", "returned_quantity")) == [(False, 10), (True, 1)]
    assert _debt(order) == Decimal("0")


def test_a_partial_return_lands_on_paid_bags_first(departments, manager):
    client = _client(departments[0])
    order, flour = _with_bonus(client)

    _return(client, manager, [{"product": flour.pk, "bags": 1}])

    order.refresh_from_db()
    assert order.total_amount == Decimal("297000.00")
    assert order.items.get(is_bonus=True).returned_quantity == 0


# Приёмка кладовщиком: до закрытия ничего не меняется, проводятся только принятые мешки.


def _debt(order):
    return order_remaining_by_id(Order.objects.filter(pk=order.pk))[order.pk]


def test_created_return_waits_and_changes_nothing(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])

    plan = _create(client, manager, [{"product": flour.pk, "bags": 4}])

    goods_return = GoodsReturn.objects.get(pk=plan["return_id"])
    assert (plan["status"], goods_return.status, goods_return.created_by) == ("pending", "pending", manager)
    assert list(goods_return.items.values_list("product_id", "product_label_snapshot", "bags", "accepted_bags")) == [
        (flour.pk, flour.plain_label, 4, None),
    ]
    assert not goods_return.lines.exists()
    assert OrderItem.objects.get(order=order).returned_quantity == 0
    assert _debt(order) == Decimal("10000")
    assert not StockMovement.objects.filter(reason="client_return").exists()
    assert not EventLog.objects.filter(event_type="goods_return").exists()
    event = EventLog.objects.get(event_type="goods_return_status")
    assert (event.user, event.order, event.payload["client_id"], event.payload["status"]) == (
        manager, None, client.pk, "pending",
    )


def test_confirmed_items_change_nothing_until_close(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    goods_return = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 4}])["return_id"])
    item = goods_return.items.get()

    confirm_goods_return_item(goods_return, item.pk, 4)
    confirm_goods_return_item(goods_return, item.pk, 3)  # пересчитал — до закрытия можно поменять

    item.refresh_from_db()
    assert item.accepted_bags == 3 and item.checked_at is not None
    assert OrderItem.objects.get(order=order).returned_quantity == 0
    assert _debt(order) == Decimal("10000")
    assert not StockMovement.objects.exists()


def test_full_acceptance_closes_as_fully_returned(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    plan = _create(client, manager, [{"product": flour.pk, "bags": 4}])

    closed = _accept(plan["return_id"], storekeeper)

    assert (closed.status, closed.accepted_by) == ("full", storekeeper)
    assert closed.accepted_at is not None
    assert OrderItem.objects.get(order=order).returned_quantity == 4
    assert _debt(order) == Decimal("6000")
    assert StockItem.objects.get(product=flour).bags == 4
    event = EventLog.objects.filter(event_type="goods_return_status").latest("id")
    assert (event.user, event.payload["status"], event.payload["accepted_bags"]) == (storekeeper, "full", 4)
    assert "принято 4 из 4 мешков — «Полностью возвращено»" in event.message


def test_partial_acceptance_credits_only_accepted_bags(manager, storekeeper, departments):
    client = _client(departments[0])
    dikhan = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")
    order = _shipped(client, [(dikhan, 10, "1000"), (korol, 10, "500")])
    plan = _create(client, manager, [{"product": dikhan.pk, "bags": 6}, {"product": korol.pk, "bags": 4}])

    closed = _accept(plan["return_id"], storekeeper, {dikhan.pk: 5, korol.pk: 4})

    assert closed.status == "partial"
    assert closed.get_status_display() == "Частично возвращено"
    assert dict(OrderItem.objects.filter(order=order).values_list("product_id", "returned_quantity")) == {
        dikhan.pk: 5, korol.pk: 4,
    }
    assert _debt(order) == Decimal("8000")  # 15000 − 5×1000 − 4×500
    assert dict(StockItem.objects.values_list("product_id", "bags")) == {dikhan.pk: 5, korol.pk: 4}
    assert sum(closed.lines.values_list("bags", flat=True)) == 9


def test_accepting_nothing_cancels_without_any_effect(manager, storekeeper, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(order, Decimal("10000"), boss, method="cash")
    plan = _create(client, manager, [{"product": flour.pk, "bags": 4}], settlement="cash")

    closed = _accept(plan["return_id"], storekeeper, {flour.pk: 0})

    assert (closed.status, closed.accepted_by) == ("cancelled", storekeeper)
    assert not closed.lines.exists()
    assert OrderItem.objects.get(order=order).returned_quantity == 0
    assert not PaymentRefund.objects.filter(payment=payment).exists()
    assert not StockMovement.objects.filter(reason="client_return").exists()
    assert not EventLog.objects.filter(event_type="goods_return").exists()


def test_close_needs_every_flour_checked(manager, storekeeper, departments):
    client = _client(departments[0])
    dikhan = _flour()
    korol = _flour("Второй сорт KOROL 50кг", "Green")
    _shipped(client, [(dikhan, 10, "1000"), (korol, 10, "500")])
    goods_return = GoodsReturn.objects.get(pk=_create(
        client, manager, [{"product": dikhan.pk, "bags": 1}, {"product": korol.pk, "bags": 1}],
    )["return_id"])
    confirm_goods_return_item(goods_return, goods_return.items.get(product=dikhan).pk, 1)

    with pytest.raises(ValidationError) as error:
        close_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "items_unchecked"
    goods_return.refresh_from_db()
    assert goods_return.status == "pending"
    assert not OrderItem.objects.filter(returned_quantity__gt=0).exists()


def test_closed_return_cannot_be_closed_or_confirmed_again(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}], storekeeper=storekeeper)
    goods_return = GoodsReturn.objects.get(pk=plan["return_id"])

    with pytest.raises(ValidationError) as again:
        close_goods_return(goods_return, storekeeper)
    with pytest.raises(ValidationError) as recount:
        confirm_goods_return_item(goods_return, goods_return.items.get().pk, 1)
    with pytest.raises(ValidationError) as cancel:
        cancel_goods_return(goods_return, manager)

    assert {again.value.detail["code"], recount.value.detail["code"], cancel.value.detail["code"]} == {
        "goods_return_not_pending",
    }
    assert OrderItem.objects.get().returned_quantity == 4
    assert StockItem.objects.get(product=flour).bags == 4


@pytest.mark.parametrize("count", [5, -1, True, "3", None, 2.0])
def test_accepted_count_is_whole_bags_up_to_requested(manager, departments, count):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 4}])["return_id"])

    with pytest.raises(ValidationError) as error:
        confirm_goods_return_item(goods_return, goods_return.items.get().pk, count)

    assert error.value.detail["code"] == "goods_return_bad_count"
    assert goods_return.items.get().accepted_bags is None


def test_unknown_item_is_not_found(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 4}])["return_id"])

    with pytest.raises(NotFound):
        confirm_goods_return_item(goods_return, goods_return.items.get().pk + 100, 1)


def test_close_refuses_when_the_debt_was_paid_in_between(manager, storekeeper, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    goods_return = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 4}])["return_id"])
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    record_staff_payment(order, Decimal("10000"), boss, method="cash")  # долга больше нет

    with pytest.raises(ValidationError) as error:
        close_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "goods_return_no_longer_fits"
    assert "Менеджер должен отменить его и создать новый" in str(error.value.detail["detail"])
    goods_return.refresh_from_db()
    assert goods_return.status == "pending"
    assert OrderItem.objects.get().returned_quantity == 0
    assert not StockMovement.objects.filter(reason="client_return").exists()


def test_close_refuses_a_return_whose_flour_was_deleted(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 4}])["return_id"])
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    goods_return.items.update(product=None)  # товар удалён физически: связь обнулилась, снимок остался

    with pytest.raises(ValidationError) as error:
        close_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "goods_return_no_longer_fits"
    assert f"«{flour.plain_label}» удалена из каталога" in str(error.value.detail["detail"])


def test_manager_cancels_a_pending_return(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 4}])["return_id"])

    cancel_goods_return(goods_return, manager)

    goods_return.refresh_from_db()
    assert (goods_return.status, goods_return.accepted_by) == ("cancelled", manager)
    with pytest.raises(ValidationError) as close:
        close_goods_return(goods_return, storekeeper)
    assert close.value.detail["code"] == "goods_return_not_pending"
    assert OrderItem.objects.get().returned_quantity == 0
    event = EventLog.objects.filter(event_type="goods_return_status").latest("id")
    assert (event.user, event.payload["status"]) == (manager, "cancelled")


def test_pending_returns_do_not_reserve_bags_and_the_late_one_no_longer_fits(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    first = _create(client, manager, [{"product": flour.pk, "bags": 8}])
    second = _create(client, manager, [{"product": flour.pk, "bags": 8}])
    _accept(first["return_id"], storekeeper)

    with pytest.raises(ValidationError) as error:
        _accept(second["return_id"], storekeeper)

    assert error.value.detail["code"] == "goods_return_no_longer_fits"
    assert "Максимум 2 мешка" in str(error.value.detail["detail"])
    assert OrderItem.objects.get().returned_quantity == 8


def test_old_image_insert_without_status_counts_as_fully_returned(departments):
    """Откат образа проводит возврат сразу и не знает колонки статуса: строка — «Полностью возвращено»."""
    client = _client(departments[0])
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO orders_goodsreturn (client_id, settlement, warehouse_id, created_at)"
            " VALUES (%s, 'debt', %s, now()) RETURNING id",
            [client.pk, get_default_warehouse().pk],
        )
        [pk] = cursor.fetchone()

    assert GoodsReturn.objects.get(pk=pk).status == "full"


# «Исправить»: закрытый возврат снова ждёт приёмки, всё, что сделало закрытие, отменяется.


def _closed(client, manager, storekeeper, lines, *, counts=None, **options):
    """Создать возврат (``lines`` — [(товар, мешков)]) и закрыть его с ``counts`` (иначе всё принято)."""
    plan = _create(client, manager, [{"product": product.pk, "bags": bags} for product, bags in lines], **options)
    return _accept(plan["return_id"], storekeeper, counts)


def test_reopen_of_a_debt_return_puts_debt_stock_and_bags_back(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    closed = _closed(client, manager, storekeeper, [(flour, 4)], warehouse=second.pk)
    checked_at = closed.items.get().checked_at
    assert (_debt(order), StockItem.objects.get(product=flour, warehouse=second).bags) == (Decimal("6000"), 4)

    reopened = reopen_goods_return(closed, storekeeper)

    reopened.refresh_from_db()
    assert (reopened.status, reopened.accepted_by, reopened.accepted_at) == ("pending", None, None)
    item = reopened.items.get()
    assert (item.accepted_bags, item.checked_at) == (4, checked_at)  # прежние числа — уже проверены
    assert not reopened.lines.exists()
    assert OrderItem.objects.get(order=order).returned_quantity == 0
    assert _debt(order) == Decimal("10000")
    assert StockItem.objects.get(product=flour, warehouse=second).bags == 0
    undo = StockMovement.objects.get(reason="client_return_undo")
    assert (undo.warehouse, undo.delta, undo.created_by) == (second, -4, storekeeper)
    assert undo.note.startswith(f"Исправление возврата товара №{closed.pk}")
    event = EventLog.objects.filter(event_type="goods_return", order=order).latest("id")
    assert (event.user, event.payload["action"], event.payload["bags"], event.payload["amount"]) == (
        storekeeper, "reopen", 4, "4000.00",
    )
    assert event.payload["cancelled_refund_ids"] == []
    status = EventLog.objects.filter(event_type="goods_return_status").latest("id")
    assert (status.user, status.payload["action"], status.payload["status"]) == (storekeeper, "reopen", "pending")
    assert status.payload["before"]["status"] == "full"
    assert status.payload["before"]["accepted_by"] == "A B"
    assert status.payload["after"] == {"status": "pending"}
    assert [(row["bags"], row["accepted_bags"]) for row in status.payload["items"]] == [(4, 4)]
    assert "был «Полностью возвращено», принято 4 из 4 мешков" in status.message


def test_reopen_of_a_cash_return_cancels_only_its_refunds(manager, storekeeper, departments, boss):
    from apps.orders.refunds import cancel_cash_refund, create_cash_refund

    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(order, Decimal("10000"), boss, method="cash")
    closed = _closed(client, manager, storekeeper, [(flour, 3)], settlement="cash")
    own = PaymentRefund.objects.get(payment=payment)
    assert own.goods_return == closed
    manual = create_cash_refund(payment, boss, amount="500", reason="Сдача клиенту")  # не этого возврата

    reopen_goods_return(closed, storekeeper)

    own.refresh_from_db()
    manual.refresh_from_db()
    assert (own.status, manual.status) == ("cancelled", "completed")
    payment.refresh_from_db()
    assert (payment.refunded_amount, payment.pending_refund_amount) == (Decimal("500"), Decimal("0"))
    assert OrderItem.objects.get(order=order).returned_quantity == 0
    order.refresh_from_db()
    assert order.paid_total == Decimal("9500")  # деньги возврата снова в оплате, чужие 500 — отданы
    assert order.payment_status == "partial"  # 10 мешков снова за клиентом: 10 000, оплачено 9 500
    money = EventLog.objects.get(event_type="payment", payload__action="cash_refund_cancelled")
    assert (money.user, money.order, money.payload["refund_id"]) == (storekeeper, order, own.pk)
    event = EventLog.objects.filter(event_type="goods_return", order=order).latest("id")
    assert (event.payload["action"], event.payload["cancelled_refund_ids"]) == ("reopen", [own.pk])
    assert event.message.endswith("из кассы — возврат снова ждёт приёмки")
    with pytest.raises(ValidationError) as twice:
        cancel_cash_refund(own.pk, boss, reason="повтор")
    assert twice.value.detail["code"] == "refund_not_cancellable"


def _setup_for_equality(client, flour, other, settlement, boss):
    now = timezone.now()
    old = _shipped(client, [(flour, 10, "1000"), (other, 10, "500")], shipped=now - timedelta(days=2))
    new = _shipped(client, [(flour, 5, "1000")], shipped=now - timedelta(days=1))
    if settlement == "cash":
        record_staff_payment(old, Decimal("15000"), boss, method="cash")
        record_staff_payment(new, Decimal("5000"), boss, method="cash")
    return [old, new]


def _money_and_stock(orders, products):
    payments = [payment for order in orders for payment in order.payments.order_by("id")]
    return {
        "returned": [
            list(order.items.order_by("id").values_list("returned_quantity", flat=True)) for order in orders
        ],
        "debt": [_debt(order) for order in orders],
        "payment_status": [Order.objects.get(pk=order.pk).payment_status for order in orders],
        "refunded": [
            Payment.objects.values_list("refunded_amount", "pending_refund_amount").get(pk=payment.pk)
            for payment in payments
        ],
        "completed_refunds": sorted(
            PaymentRefund.objects.filter(payment__in=payments, status="completed").values_list("amount", flat=True),
        ),
        "stock": [StockItem.objects.get(product=product).bags for product in products],
        "lines": sorted(
            GoodsReturnLine.objects.filter(order_item__order__in=orders).values_list(
                "order_item__order_id", "bags", "amount",
            ),
        ),
    }


@pytest.mark.parametrize("settlement", ["debt", "cash"])
def test_reopen_change_and_close_ends_like_one_close_with_the_new_counts(
    manager, storekeeper, departments, boss, settlement,
):
    once_client = _client(departments[0])
    fixed_client = Client.objects.create_with_user(
        first_name="Второй", phone="+7 (705) 777-77-77", department=departments[0],
    )
    once_flour, once_other = _flour("Мука А 50кг"), _flour("Мука Б 50кг", "Green")
    fixed_flour, fixed_other = _flour("Мука В 50кг"), _flour("Мука Г 50кг", "Green")
    once_orders = _setup_for_equality(once_client, once_flour, once_other, settlement, boss)
    fixed_orders = _setup_for_equality(fixed_client, fixed_flour, fixed_other, settlement, boss)

    _closed(once_client, manager, storekeeper, [(once_flour, 8), (once_other, 4)],
            counts={once_flour.pk: 6, once_other.pk: 3}, settlement=settlement)
    mistaken = _closed(fixed_client, manager, storekeeper, [(fixed_flour, 8), (fixed_other, 4)],
                       settlement=settlement)  # «принято всё» — ошибка
    reopened = reopen_goods_return(mistaken, storekeeper)
    for item in reopened.items.all():
        confirm_goods_return_item(reopened, item.pk, {fixed_flour.pk: 6, fixed_other.pk: 3}[item.product_id])
    corrected = close_goods_return(reopened, storekeeper)

    assert corrected.status == "partial"
    once = _money_and_stock(once_orders, [once_flour, once_other])
    fixed = _money_and_stock(fixed_orders, [fixed_flour, fixed_other])
    once_ids = {order.pk: index for index, order in enumerate(once_orders)}
    fixed_ids = {order.pk: index for index, order in enumerate(fixed_orders)}
    once["lines"] = sorted((once_ids[pk], bags, amount) for pk, bags, amount in once["lines"])
    fixed["lines"] = sorted((fixed_ids[pk], bags, amount) for pk, bags, amount in fixed["lines"])
    assert fixed == once
    assert once["stock"] == [6, 3]
    assert [(order, bags) for order, bags, _amount in once["lines"]] == [(0, 1), (0, 3), (1, 5)]
    assert bool(once["completed_refunds"]) == (settlement == "cash")


def test_reopen_of_a_partial_return_lets_the_storekeeper_accept_the_rest(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    closed = _closed(client, manager, storekeeper, [(flour, 4)], counts={flour.pk: 3})
    assert closed.status == "partial"

    reopened = reopen_goods_return(closed, storekeeper)
    assert reopened.items.get().accepted_bags == 3
    confirm_goods_return_item(reopened, reopened.items.get().pk, 4)
    again = close_goods_return(reopened, storekeeper)

    assert again.status == "full"
    assert OrderItem.objects.get(order=order).returned_quantity == 4
    assert StockItem.objects.get(product=flour).bags == 4
    assert _debt(order) == Decimal("6000")


def test_reopen_of_a_return_closed_with_nothing_accepted(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    closed = _closed(client, manager, storekeeper, [(flour, 4)], counts={flour.pk: 0})
    assert closed.status == "cancelled"

    reopened = reopen_goods_return(closed, storekeeper)

    assert (reopened.status, reopened.items.get().accepted_bags) == ("pending", 0)
    assert not StockMovement.objects.exists()
    assert not EventLog.objects.filter(event_type="goods_return").exists()  # проводить было нечего
    confirm_goods_return_item(reopened, reopened.items.get().pk, 2)
    assert close_goods_return(reopened, storekeeper).status == "partial"
    assert OrderItem.objects.get(order=order).returned_quantity == 2


def test_reopen_of_the_storekeepers_own_cancel_changes_nothing(manager, storekeeper, departments):
    """Кладовщик отменил возврат, когда муку уже посчитал: проведено не было — откатывать нечего."""
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    goods_return = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 4}])["return_id"])
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    cancel_goods_return(goods_return, storekeeper, by_storekeeper=True)

    reopened = reopen_goods_return(goods_return, storekeeper)

    assert (reopened.status, reopened.items.get().accepted_bags, reopened.closed_by_storekeeper) == ("pending", 4, False)
    assert OrderItem.objects.get().returned_quantity == 0
    assert not StockMovement.objects.exists()


@pytest.mark.parametrize("closed_first", [False, True])
def test_reopen_of_a_managers_cancel_is_refused(manager, storekeeper, departments, boss, closed_first):
    """Менеджер решил, что возврата не будет: кладовщик не вернёт его на приёмку и не отдаст деньги из кассы."""
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(order, Decimal("10000"), boss, method="cash")
    goods_return = GoodsReturn.objects.get(
        pk=_create(client, manager, [{"product": flour.pk, "bags": 4}], settlement="cash")["return_id"],
    )
    if closed_first:  # кладовщик закрыл и исправил, менеджер отменил исправленный
        reopen_goods_return(_accept(goods_return.pk, storekeeper), storekeeper)
    confirm_goods_return_item(goods_return, goods_return.items.get().pk, 4)
    cancel_goods_return(goods_return, manager)

    with pytest.raises(ValidationError) as error:
        reopen_goods_return(goods_return, storekeeper)

    assert error.value.detail["code"] == "goods_return_cannot_reopen"
    assert "его отменил менеджер" in str(error.value.detail["detail"])
    goods_return.refresh_from_db()
    assert (goods_return.status, goods_return.accepted_by) == ("cancelled", manager)
    assert not PaymentRefund.objects.filter(payment=payment, status="completed").exists()
    assert OrderItem.objects.get(order=order).returned_quantity == 0


def test_reopen_twice_or_of_a_pending_return_is_refused(manager, storekeeper, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    closed = _closed(client, manager, storekeeper, [(flour, 4)])
    reopen_goods_return(closed, storekeeper)

    with pytest.raises(ValidationError) as twice:
        reopen_goods_return(closed, storekeeper)

    assert twice.value.detail["code"] == "goods_return_not_closed"
    assert OrderItem.objects.get(order=order).returned_quantity == 0
    assert StockItem.objects.get(product=flour).bags == 0
    assert StockMovement.objects.filter(reason="client_return_undo").count() == 1


def test_reopen_refuses_while_an_order_of_the_return_is_in_the_trash(manager, storekeeper, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(order, Decimal("10000"), boss, method="cash")
    closed = _closed(client, manager, storekeeper, [(flour, 4)], settlement="cash")
    Order.all_objects.filter(pk=order.pk).update(deleted_at=timezone.now())

    with pytest.raises(ValidationError) as error:
        reopen_goods_return(closed, storekeeper)

    assert error.value.detail["code"] == "goods_return_cannot_reopen"
    assert str(error.value.detail["detail"]) == (
        f"Возврат №{closed.pk} нельзя вернуть на приёмку: заказ #{order.pk} в корзине —"
        " попросите менеджера восстановить его, потом нажмите «Исправить» снова"
    )
    closed.refresh_from_db()
    assert (closed.status, closed.lines.count()) == ("full", 1)
    assert OrderItem.objects.get(order=order).returned_quantity == 4
    assert PaymentRefund.objects.get(payment=payment).status == "completed"
    assert StockItem.objects.get(product=flour).bags == 4

    Order.all_objects.filter(pk=order.pk).update(purged_at=timezone.now())  # удалён из корзины навсегда
    with pytest.raises(ValidationError) as purged:
        reopen_goods_return(closed, storekeeper)
    assert str(purged.value.detail["detail"]).endswith(f"заказ #{order.pk} удалён навсегда")

    Order.all_objects.filter(pk=order.pk).update(deleted_at=None, purged_at=None)  # восстановили — исправить можно
    assert reopen_goods_return(closed, storekeeper).status == "pending"


def _cash_return_closed_by_an_old_release(client, manager, storekeeper, boss, flour):
    """Оплаченный заказ и «Из кассы» на 3 мешка, закрытый релизом без связи возврата оплаты (откат деплоя)."""
    order = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(order, Decimal("10000"), boss, method="cash")
    closed = _closed(client, manager, storekeeper, [(flour, 3)], settlement="cash")
    PaymentRefund.objects.filter(payment=payment).update(goods_return=None)
    return order, payment, closed


def test_reopen_finds_the_cash_refund_of_an_old_release_close_by_its_event(manager, storekeeper, departments, boss):
    """Связи нет — выплату находит событие закрытия: деньги снова в оплате, а не новый долг клиента."""
    from apps.orders.refunds import create_cash_refund

    client = _client(departments[0])
    order, payment, closed = _cash_return_closed_by_an_old_release(client, manager, storekeeper, boss, _flour())
    own = PaymentRefund.objects.get(payment=payment)
    manual = create_cash_refund(payment, boss, amount="500", reason="Сдача клиенту")  # не этого возврата

    reopen_goods_return(closed, storekeeper)

    own.refresh_from_db()
    manual.refresh_from_db()
    assert (own.status, own.goods_return_id) == ("cancelled", closed.pk)
    assert (manual.status, manual.goods_return_id) == ("completed", None)
    order.refresh_from_db()
    assert order.paid_total == Decimal("9500")
    assert _debt(order) == Decimal("500")  # только чужие 500, не 3 000 возврата
    event = EventLog.objects.filter(event_type="goods_return", order=order).latest("id")
    assert event.payload["cancelled_refund_ids"] == [own.pk]


def test_reopen_refuses_when_the_cash_refunds_do_not_add_up(manager, storekeeper, departments, boss):
    """Ни связи, ни события закрытия: выплату не угадываем по сумме — исправлять нельзя, ничего не меняется."""
    client = _client(departments[0])
    flour = _flour()
    order, payment, closed = _cash_return_closed_by_an_old_release(client, manager, storekeeper, boss, flour)
    EventLog.objects.filter(event_type="goods_return").delete()

    with pytest.raises(ValidationError) as error:
        reopen_goods_return(closed, storekeeper)

    assert error.value.detail["code"] == "goods_return_cannot_reopen"
    assert f"выплаты из кассы по заказу #{order.pk} не сходятся с возвратом" in str(error.value.detail["detail"])
    closed.refresh_from_db()
    assert (closed.status, closed.lines.count()) == ("full", 1)
    assert PaymentRefund.objects.get(payment=payment).status == "completed"
    assert OrderItem.objects.get(order=order).returned_quantity == 3
    assert StockItem.objects.get(product=flour).bags == 3


def test_reopen_takes_the_bags_back_even_after_they_were_shipped(manager, storekeeper, departments):
    from apps.warehouse.services import deduct_stock

    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    closed = _closed(client, manager, storekeeper, [(flour, 4)])
    deduct_stock(flour, 3, storekeeper)  # три принятых мешка уже уехали

    reopen_goods_return(closed, storekeeper)

    assert StockItem.objects.get(product=flour).bags == -3
    negative = EventLog.objects.get(event_type="stock_negative")
    assert (negative.payload["had"], negative.payload["deduct"]) == (1, 4)


def test_reopen_is_out_of_reach_for_another_department(manager, storekeeper, user_with_perms, departments):
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    closed = _closed(client, manager, storekeeper, [(flour, 4)])
    stranger = user_with_perms("city-keeper", codes=["storekeeper.confirm"], department=departments[1])

    with pytest.raises(PermissionDenied):
        reopen_goods_return(closed, stranger)

    closed.refresh_from_db()
    assert closed.status == "full"


def test_migration_links_closed_cash_returns_to_their_refunds(manager, storekeeper, departments, boss):
    import importlib

    from django.apps import apps as django_apps

    from apps.orders.refunds import create_cash_refund

    migration = importlib.import_module("apps.orders.migrations.0054_link_goods_return_refunds")
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(order, Decimal("10000"), boss, method="cash")
    closed = _closed(client, manager, storekeeper, [(flour, 3)], settlement="cash")
    manual = create_cash_refund(payment, boss, amount="500", reason="Сдача клиенту")
    PaymentRefund.objects.update(goods_return=None)  # как до миграции

    migration.link_goods_return_refunds(django_apps, None)

    assert list(closed.refunds.values_list("amount", flat=True)) == [Decimal("3000.00")]
    manual.refresh_from_db()
    assert manual.goods_return is None


def test_migration_marks_returns_closed_by_the_storekeeper(manager, storekeeper, departments):
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("apps.orders.migrations.0055_goodsreturn_closed_by_storekeeper")
    client = _client(departments[0])
    flour = _flour()
    _shipped(client, [(flour, 10, "1000")])
    by_keeper = _closed(client, manager, storekeeper, [(flour, 2)], counts={flour.pk: 0})
    by_manager = GoodsReturn.objects.get(pk=_create(client, manager, [{"product": flour.pk, "bags": 2}])["return_id"])
    cancel_goods_return(by_manager, manager)
    GoodsReturn.objects.update(closed_by_storekeeper=False)  # как до миграции

    migration.mark_storekeeper_closed(django_apps, None)

    assert dict(GoodsReturn.objects.values_list("pk", "closed_by_storekeeper")) == {
        by_keeper.pk: True, by_manager.pk: False,
    }
