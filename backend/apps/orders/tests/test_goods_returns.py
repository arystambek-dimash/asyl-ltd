"""«Возврат» по клиенту: мешки раскладываются по отгруженным заказам — от новой отгрузки к старой."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.goods_returns import record_goods_return
from apps.orders.models import GoodsReturn, Order, OrderItem, PaymentRefund
from apps.orders.querysets import order_remaining_by_id, with_order_amounts
from apps.orders.services import record_staff_payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem, StockMovement, Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(user_with_perms, departments):
    return user_with_perms(
        "mill-manager", codes=["orders.edit", "payments.confirm", "payments.create"], department=departments[0],
    )


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


def _return(client, user, lines, *, settlement="debt", preview=False, warehouse=None):
    return record_goods_return(
        client, user, settlement=settlement, warehouse=warehouse, lines=lines, preview=preview,
    )


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

    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}], preview=True)

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


def test_cash_mode_refunds_paid_orders_from_the_till(manager, departments, boss):
    client = _client(departments[0])
    flour = _flour()
    paid = _shipped(client, [(flour, 10, "1000")])
    payment = record_staff_payment(paid, Decimal("10000"), boss, method="cash")
    _shipped(client, [(flour, 10, "1000")], shipped=timezone.now() - timedelta(days=1))  # в долге — не для кассы

    plan = _return(client, manager, [{"product": flour.pk, "bags": 3}], settlement="cash")

    assert [row["order_id"] for row in plan["orders"]] == [paid.pk]
    refund = PaymentRefund.objects.get(payment=payment)
    assert (refund.amount, refund.method, refund.status) == (Decimal("3000.00"), "cash", "completed")
    assert refund.reason == f"Возврат товара №{plan['return_id']}"
    paid.refresh_from_db()
    assert paid.payment_status == "settled"  # 7 мешков = 7000, оплачено 10000 − 3000


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


def test_return_puts_bags_on_the_chosen_warehouse_and_logs_each_order(manager, departments):
    client = _client(departments[0])
    flour = _flour()
    order = _shipped(client, [(flour, 10, "1000")])
    second = Warehouse.objects.create(code="mill-2", name="Мельница 2")

    plan = _return(client, manager, [{"product": flour.pk, "bags": 4}], warehouse=second.pk)

    assert StockItem.objects.get(product=flour, warehouse=second).bags == 4
    assert StockMovement.objects.get(product=flour, warehouse=second).reason == "client_return"
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


def test_api_previews_and_records_a_return(auth_client, manager, departments):
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
