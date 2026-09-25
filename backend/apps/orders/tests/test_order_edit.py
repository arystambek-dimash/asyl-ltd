"""Редактирование заказа: позиции и цены — до начала загрузки."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from apps.cameras.models import AiCountingSession
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders import services
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import OrderSerializer
from apps.sales.models import Department

pytestmark = pytest.mark.django_db

_seq = [0]


def _product(bags=500):
    from apps.warehouse.models import StockItem
    _seq[0] += 1
    p = Product.objects.create(
        name=f"P{_seq[0]}", color="Red", weight_kg="50")
    if bags:
        StockItem.objects.create(product=p, bags=bags)
    return p


def _order(status="pending", unit_price="100.00"):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    order = Order.objects.create(client=client, status=status)
    OrderItem.objects.create(order=order, product=_product(), quantity=2,
                             unit_price=unit_price)
    return order


def test_edit_items_with_prices(manager, api_as):
    o = _order(status="pending")
    p2 = _product()
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 5}],
        "prices": {str(p2.id): "150.00"},
    }, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    items = list(o.items.all())
    assert len(items) == 1
    assert items[0].product_id == p2.id
    assert items[0].unit_price == Decimal("150.00")
    assert o.total_amount == Decimal("750.00")


def test_edit_items_rejects_new_archived_product_but_keeps_historical(manager, api_as):
    o = _order(status="pending")
    kept = o.items.get().product
    archived = _product()
    Product.objects.filter(pk__in=[kept.pk, archived.pk]).update(is_active=False)

    added = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": archived.id, "quantity": 1}],
        "prices": {str(archived.id): "100.00"},
    }, format="json")
    assert added.status_code == 400
    assert added.data["detail"]["items"] == ["Архивный товар нельзя добавлять в заказ"]

    kept_response = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": kept.id, "quantity": 3}],
        "prices": {str(kept.id): "100.00"},
    }, format="json")
    assert kept_response.status_code == 200


def test_edit_items_rejects_duplicate_product_rows(manager, api_as):
    o = _order(status="pending")
    p2 = _product()
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 1}, {"product": p2.id, "quantity": 2}],
        "prices": {str(p2.id): "100.00"},
    }, format="json")
    assert r.status_code == 400
    assert r.data["detail"]["items"] == ["Объедините повторяющиеся товары в одну строку"]


def test_edit_items_on_confirmed_requires_prices(manager, api_as):
    o = _order(status="confirmed")
    p2 = _product()
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 3}],
    }, format="json")
    assert r.status_code == 400
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 3}],
        "prices": {str(p2.id): "110.00"},
    }, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.total_amount == Decimal("330.00")


def test_edit_items_allowed_while_awaiting_loading(manager, api_as):
    """«Ожидает загрузки» (arrived): машина въехала, но состав ещё можно менять."""
    o = _order(status="arrived")
    p2 = _product()
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 4}],
        "prices": {str(p2.id): "130.00"},
    }, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.total_amount == Decimal("520.00")


def test_edit_items_on_arrived_requires_prices(manager, api_as):
    """Без цен зафиксированная договорная цена не должна слетать на базовую."""
    o = _order(status="arrived")
    p2 = _product()
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 3}],
    }, format="json")
    assert r.status_code == 400
    o.refresh_from_db()
    # Старая позиция с ценой не тронута (транзакция откатилась).
    assert o.items.first().unit_price == Decimal("100.00")


def test_edit_cannot_empty_items(manager, api_as):
    o = _order(status="pending")
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {"items": []}, format="json")
    assert r.status_code == 400


def test_edit_fields_without_items(manager, api_as):
    o = _order(status="confirmed")
    store = Store.objects.create(client=o.client, name="S1")
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "arrival_date": "2026-07-10", "store": store.id,
    }, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert str(o.arrival_date) == "2026-07-10"
    assert o.store_id == store.id
    # Позиции не тронуты.
    assert o.items.count() == 1


def test_stale_scalar_patch_cannot_restore_pre_ai_status(manager):
    stale_order = _order(status="confirmed")
    Order.objects.filter(pk=stale_order.pk).update(
        status="loading",
        loading_camera="cam2",
    )
    serializer = OrderSerializer(
        stale_order,
        data={"notes": "Проверено оператором"},
        partial=True,
        context={"request": SimpleNamespace(user=manager)},
    )
    assert serializer.is_valid(), serializer.errors

    serializer.save()

    stale_order.refresh_from_db()
    assert stale_order.status == "loading"
    assert stale_order.loading_camera == "cam2"
    assert stale_order.notes == "Проверено оператором"


def test_stale_serializer_cannot_update_archived_order(manager):
    stale_order = _order(status="pending")
    serializer = OrderSerializer(
        stale_order,
        data={"notes": "Устаревшая вкладка"},
        partial=True,
        context={"request": SimpleNamespace(user=manager)},
    )
    assert serializer.is_valid(), serializer.errors
    services.soft_delete_order(stale_order, manager)

    with pytest.raises(ValidationError) as caught:
        serializer.save()

    assert caught.value.detail["code"] == "order_not_active"
    stale_order.refresh_from_db()
    assert stale_order.deleted_at is not None
    assert stale_order.notes == ""


def test_generic_patch_cannot_change_status_or_camera(manager, api_as):
    order = _order(status="loading")
    order.loading_camera = "cam2"
    order.save(update_fields=["loading_camera"])
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=manager,
    )

    response = api_as(manager).patch(
        f"/api/orders/{order.id}/",
        {"status": "shipped", "loading_camera": "cam3"},
        format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam2"


def test_transport_type_change_rejects_open_ai_reservation(manager, api_as):
    order = _order(status="confirmed")
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=manager,
    )

    response = api_as(manager).patch(
        f"/api/orders/{order.id}/",
        {"transport_type": "train"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "ai_session_active"
    order.refresh_from_db()
    assert order.transport_type == "truck"


def test_department_change_rejects_open_ai_reservation(manager, api_as):
    order = _order(status="confirmed")
    department = Department.objects.create(
        code="safe-scope",
        name="Безопасный отдел",
        color="#2457C5",
        is_active=True,
    )
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=manager,
    )

    response = api_as(manager).patch(
        f"/api/orders/{order.id}/",
        {"department": department.code},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "ai_session_active"
    order.refresh_from_db()
    assert order.department != department.code


def test_patch_cannot_change_settlement_intent(manager, api_as):
    """Способ расчёта меняют сервисы оплаты и долга, а не общий PATCH заказа."""
    o = _order(status="confirmed")

    response = api_as(manager).patch(
        f"/api/orders/{o.id}/",
        {"settlement_intent": "instant"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["settlement_intent"] == "debt"
    o.refresh_from_db()
    assert o.settlement_intent == "debt"
    assert o.payment_method == "debt"


def test_edit_order_note(manager, api_as):
    o = _order(status="shipped")
    r = api_as(manager).patch(
        f"/api/orders/{o.id}/", {"notes": "Доставить до 18:00"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.notes == "Доставить до 18:00"
    assert r.data["notes"] == "Доставить до 18:00"


def test_edit_client_is_locked(manager, api_as):
    o = _order(status="pending")
    other = Client.objects.create_with_user(first_name="Z", last_name="Z", phone="z")
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {"client": other.id}, format="json")
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.client.user.first_name == "A"


def test_foreign_store_rejected(manager, api_as):
    o = _order(status="pending")
    stranger = Client.objects.create_with_user(first_name="S", last_name="S", phone="s")
    foreign_store = Store.objects.create(client=stranger, name="Чужой")
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {"store": foreign_store.id}, format="json")
    assert r.status_code == 400


def test_order_requires_stock_on_create(manager, api_as):
    """Заказ принимается только на товар, имеющийся на складе."""
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    empty = _product(bags=0)  # складской карточки нет вовсе
    r = api_as(manager).post("/api/orders/", {
        "client": client.id,
        "items": [{"product": empty.id, "quantity": 1}],
        "prices": {str(empty.id): "100.00"},
    }, format="json")
    assert r.status_code == 400
    assert "наличии" in str(r.data.get("detail", ""))


def test_edit_requires_stock_for_new_items(manager, api_as):
    o = _order(status="pending")
    from apps.warehouse.models import StockItem
    zero = _product(bags=0)
    StockItem.objects.create(product=zero, bags=0)  # карточка есть, остаток 0
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": zero.id, "quantity": 1}],
        "prices": {str(zero.id): "100.00"},
    }, format="json")
    assert r.status_code == 400
    assert "наличии" in str(r.data.get("detail", ""))


def test_edit_requires_orders_edit_perm(operator, api_as):
    # У оператора нет orders.edit — редактирование запрещено.
    o = _order(status="pending")
    r = api_as(operator).patch(f"/api/orders/{o.id}/", {"arrival_date": "2026-07-10"}, format="json")
    assert r.status_code == 403


def test_edit_rejects_prices_that_are_not_an_object(manager, api_as):
    o = _order(status="pending")
    product = o.items.get().product
    r = api_as(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": product.id, "quantity": 3}],
        "prices": ["100.00"],
    }, format="json")
    assert r.status_code == 400
    assert r.data["detail"]["prices"] == ["Ожидается объект цен по идентификаторам товаров"]


def test_superuser_department_is_not_forced_on_new_order(manager, api_as):
    """Суперюзер видит все отделы (sales.access): свой отдел ему не подставляется."""
    own = Department.objects.create(code="own", name="Свой")
    chosen = Department.objects.create(code="chosen", name="Выбранный")
    manager.is_superuser = True
    manager.save(update_fields=["is_superuser"])
    manager.employee.sales_department = own
    manager.employee.save(update_fields=["sales_department"])
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    product = _product()

    r = api_as(manager).post("/api/orders/", {
        "client": client.id,
        "department": chosen.code,
        "items": [{"product": product.id, "quantity": 1}],
        "prices": {str(product.id): "100.00"},
    }, format="json")

    assert r.status_code == 201, r.data
    assert Order.objects.get(pk=r.data["id"]).department == chosen.code
