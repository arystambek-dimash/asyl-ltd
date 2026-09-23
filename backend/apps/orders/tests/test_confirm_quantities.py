"""Окно подтверждения заявки: остаток склада, «8 из 10», номер транспорта."""
import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderItem
from apps.orders.services import confirm_order, confirm_stock_context
from apps.orders.statuses import AWAITING_SHIPMENT_STATUSES
from apps.orders.transport import set_order_transport
from apps.sales.models import Department
from apps.warehouse.models import StockItem, Warehouse
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def department():
    department, _ = Department.objects.get_or_create(code="main", defaults={"name": "Основной"})
    return department


@pytest.fixture
def flour():
    return Product.objects.create(name="Мука 1с", color="White", weight_kg="50", price="100.00")


@pytest.fixture
def bran():
    return Product.objects.create(name="Отруби", color="Red", weight_kg="25", price="50.00")


@pytest.fixture
def portal_user(make_user):
    return make_user(username="confirm-cli", client=True)


@pytest.fixture
def customer(portal_user, department):
    return Client.objects.create_with_user(
        user=portal_user, first_name="Азамат", last_name="К", phone="confirm-1", department=department,
        country="Кыргызстан")


def _order(client, *items, status="pending", warehouse=None, **fields):
    order = Order.objects.create(client=client, status=status, warehouse=warehouse, **fields)
    for product, quantity in items:
        OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price="100.00")
    return order


def _notes(order):
    return list(Notification.objects.filter(client=order.client).values_list("text", flat=True))


def _confirm(api, order, **payload):
    return api.post(f"/api/orders/{order.pk}/confirm/", {"department": "main", **payload}, format="json")


def test_awaiting_shipment_statuses_are_one_shared_set():
    from apps.shipments import services as shipment_services

    assert AWAITING_SHIPMENT_STATUSES == ("confirmed", "arrived", "loading", "loaded")
    assert not hasattr(shipment_services, "DISPATCHABLE_STATUSES")


# --- GET /orders/{id}/confirm-context/ ---------------------------------------


def test_confirm_context_reports_stock_and_what_waits_for_shipment(
    auth_client, manager, customer, flour, bran
):
    main = Warehouse.objects.get(code="main")
    other = Warehouse.objects.create(code="west", name="Западный склад")
    StockItem.objects.create(product=flour, warehouse=main, bags=35)
    StockItem.objects.create(product=flour, warehouse=other, bags=100)
    request = _order(customer, (flour, 10), (bran, 4), warehouse=main)
    for status in AWAITING_SHIPMENT_STATUSES:
        _order(customer, (flour, 3), status=status, warehouse=main)
    # Не ждут отгрузки: заявка, отгруженный, отменённый, в корзине, другой склад.
    _order(customer, (flour, 50), status="pending", warehouse=main)
    _order(customer, (flour, 50), status="shipped", warehouse=main)
    _order(customer, (flour, 50), status="cancelled", warehouse=main)
    _order(customer, (flour, 50), status="confirmed", warehouse=main, deleted_at=timezone.now())
    _order(customer, (flour, 50), status="confirmed", warehouse=other)

    response = auth_client(manager).get(f"/api/orders/{request.pk}/confirm-context/")

    assert response.status_code == 200, response.data
    flour_item, bran_item = request.items.order_by("id")
    assert response.data["items"] == {
        str(flour_item.pk): {"on_hand": 35, "awaiting_shipment": 12},
        str(bran_item.pk): {"on_hand": 0, "awaiting_shipment": 0},
    }
    assert response.data["transport_locked"] is False
    # Страна клиента — маска пустого поля номера.
    assert response.data["client_country"] == "Кыргызстан"


def test_confirm_context_uses_the_order_warehouse(auth_client, manager, customer, flour):
    other = Warehouse.objects.create(code="west", name="Западный склад")
    StockItem.objects.create(product=flour, warehouse=other, bags=9)
    _order(customer, (flour, 4), status="loading", warehouse=other)
    request = _order(customer, (flour, 10), warehouse=other)

    response = auth_client(manager).get(f"/api/orders/{request.pk}/confirm-context/")

    item = request.items.get()
    assert response.data["items"] == {str(item.pk): {"on_hand": 9, "awaiting_shipment": 4}}


def test_confirm_context_counts_an_order_without_warehouse_on_the_default_one(customer, flour):
    default = get_default_warehouse()
    StockItem.objects.create(product=flour, warehouse=default, bags=6)
    _order(customer, (flour, 2), status="confirmed", warehouse=default)
    request = _order(customer, (flour, 10))
    item = request.items.get()
    # Склад заказу закрепляет база с первой позицией; до неё он не задан.
    request.warehouse = None

    assert confirm_stock_context(request) == {str(item.pk): {"on_hand": 6, "awaiting_shipment": 2}}


def test_confirm_context_marks_a_client_number_read_only(auth_client, manager, portal_user, customer, flour):
    request = _order(customer, (flour, 10))
    set_order_transport(request, portal_user, truck="403BJN13")

    response = auth_client(manager).get(f"/api/orders/{request.pk}/confirm-context/")

    assert response.data["transport_locked"] is True


def test_confirm_context_needs_confirm_permission(auth_client, user_with_perms, customer, flour):
    request = _order(customer, (flour, 10))
    viewer = user_with_perms("viewer", codes=["orders.view"])

    assert auth_client(viewer).get(f"/api/orders/{request.pk}/confirm-context/").status_code == 403


def test_confirm_context_is_open_for_a_request_of_a_client_without_department(
    auth_client, user_with_perms, department, flour
):
    city = Department.objects.create(code="city", name="Город")
    cashier = user_with_perms("city-cashier", codes=["orders.view", "orders.confirm"])
    cashier.employee.sales_department = city
    cashier.employee.save(update_fields=["sales_department"])
    orphan = Client.objects.create_with_user(first_name="Без", last_name="Отдела", phone="confirm-2")
    request = _order(orphan, (flour, 10))

    response = auth_client(cashier).get(f"/api/orders/{request.pk}/confirm-context/")

    assert response.status_code == 200, response.data


def test_confirm_context_follows_the_shared_request_queue(auth_client, user_with_perms, flour):
    mill = Department.objects.create(code="mill", name="Мельница")
    city = Department.objects.create(code="city", name="Город")
    foreign = Client.objects.create_with_user(first_name="Чужой", phone="confirm-3", department=city)
    request = _order(foreign, (flour, 10))

    def cashier(username, codes):
        user = user_with_perms(username, codes=["orders.view", "orders.confirm", *codes])
        user.employee.sales_department = mill
        user.employee.save(update_fields=["sales_department"])
        return auth_client(user)

    url = f"/api/orders/{request.pk}/confirm-context/"
    assert cashier("own-only", []).get(url).status_code == 404
    assert cashier("all-requests", ["orders.confirm_all"]).get(url).status_code == 200


# --- POST /orders/{id}/confirm/ with quantities -------------------------------


def test_confirm_cuts_quantity_in_place_logs_and_notifies_once(auth_client, manager, customer, flour, bran):
    request = _order(customer, (flour, 10), (bran, 4))
    flour_item, bran_item = request.items.order_by("id")

    response = _confirm(auth_client(manager), request, quantities={str(flour_item.pk): 8, str(bran_item.pk): 4})

    assert response.status_code == 200, response.data
    request.refresh_from_db()
    assert request.status == "confirmed"
    # Позиции правятся на месте: id те же, склад и история ссылаются на них.
    assert list(request.items.order_by("id").values_list("id", "quantity")) == [
        (flour_item.pk, 8), (bran_item.pk, 4)]
    assert response.data["total_amount"] == "1200.00"
    event = EventLog.objects.get(order=request, event_type="status", payload__to="confirmed")
    assert event.message == f"Заказ подтверждён: 12 из 14 меш. ({flour_item.product_label} — 8 из 10)"
    assert event.payload["quantity_changes"] == [
        {"item_id": flour_item.pk, "product": flour_item.product_label, "requested": 10, "confirmed": 8}
    ]
    assert _notes(request) == [
        f"Заявка №{request.pk} подтверждена: {flour_item.product_label} — 8 из 10 меш."
    ]


def test_confirm_without_cut_keeps_the_plain_message_and_does_not_notify(auth_client, manager, customer, flour):
    request = _order(customer, (flour, 10))
    item = request.items.get()

    response = _confirm(auth_client(manager), request, quantities={str(item.pk): 10})

    assert response.status_code == 200, response.data
    event = EventLog.objects.get(order=request, event_type="status", payload__to="confirmed")
    assert event.message == "Заказ подтверждён"
    assert "quantity_changes" not in event.payload
    assert _notes(request) == []


def test_confirm_lists_three_cut_items_and_counts_the_rest(manager, customer, department):
    products = [
        Product.objects.create(name=f"Товар {index}", color="White", weight_kg="50") for index in range(5)
    ]
    request = _order(customer, *((product, 10) for product in products))
    items = list(request.items.order_by("id"))

    confirm_order(request, manager, department="main", quantities={item.pk: 9 for item in items})

    event = EventLog.objects.get(order=request, event_type="status", payload__to="confirmed")
    shown = ", ".join(f"{item.product_label} — 9 из 10" for item in items[:3])
    assert event.message == f"Заказ подтверждён: 45 из 50 меш. ({shown} и ещё 2)"
    assert len(event.payload["quantity_changes"]) == 5
    shown_notes = ", ".join(f"{item.product_label} — 9 из 10 меш." for item in items[:3])
    assert _notes(request) == [f"Заявка №{request.pk} подтверждена: {shown_notes} и ещё 2"]


def test_confirm_messages_fit_the_500_character_columns(manager, customer, department, flour):
    request = _order(customer)
    for index in range(3):
        # Снимок названия товара длиной до 255 знаков — три таких не влезают в 500.
        OrderItem.objects.create(order=request, product=flour, quantity=10, unit_price="100.00",
                                 product_label_snapshot=f"{index}" + "Мука" * 60)

    confirm_order(request, manager, department="main",
                  quantities={item.pk: 1 for item in request.items.all()})

    event = EventLog.objects.get(order=request, event_type="status", payload__to="confirmed")
    assert event.message.startswith("Заказ подтверждён: 3 из 30 меш.")
    assert len(event.message) <= 500
    assert len(_notes(request)[0]) <= 500


def test_confirm_refuses_more_than_requested(auth_client, manager, customer, flour):
    request = _order(customer, (flour, 10))
    item = request.items.get()

    response = _confirm(auth_client(manager), request, quantities={str(item.pk): 11})

    assert response.status_code == 400
    assert response.data["code"] == "quantity_exceeds_request"
    request.refresh_from_db()
    item.refresh_from_db()
    assert (request.status, item.quantity) == ("pending", 10)


@pytest.mark.parametrize("quantity", [0, -1, "abc", True])
def test_confirm_refuses_an_impossible_quantity(auth_client, manager, customer, flour, quantity):
    request = _order(customer, (flour, 10))
    item = request.items.get()

    response = _confirm(auth_client(manager), request, quantities={str(item.pk): quantity})

    assert response.status_code == 400
    request.refresh_from_db()
    assert request.status == "pending"


def test_confirm_refuses_an_item_of_another_order(auth_client, manager, customer, flour):
    request = _order(customer, (flour, 10))
    stranger = _order(customer, (flour, 5)).items.get()

    response = _confirm(auth_client(manager), request, quantities={str(stranger.pk): 5})

    assert response.status_code == 400
    assert response.data["code"] == "invalid_item"
    assert "обновите заявку" in response.data["detail"]
    request.refresh_from_db()
    assert request.status == "pending"


def test_confirm_quantity_check_in_the_service_itself(manager, customer, department, flour):
    request = _order(customer, (flour, 10))
    item = request.items.get()

    with pytest.raises(ValidationError) as exc_info:
        confirm_order(request, manager, department="main", quantities={item.pk: 0})

    assert exc_info.value.detail["code"] == "invalid_quantity"


# --- POST /orders/{id}/confirm/ with transport --------------------------------


def test_confirm_writes_truck_and_trailer_without_a_transport_notice(auth_client, manager, customer, flour):
    request = _order(customer, (flour, 10))

    response = _confirm(auth_client(manager), request, truck_number="07 kg 695 adt", trailer_number="07kg837pb")

    assert response.status_code == 200, response.data
    request.refresh_from_db()
    assert (request.truck_number, request.trailer_number) == ("07KG695ADT", "07KG837PB")
    assert request.truck_number_set_by == manager
    assert request.status == "confirmed"
    # Номер при подтверждении — не «смена машины»: клиенту о нём не пишем.
    assert _notes(request) == []


def test_confirm_treats_empty_numbers_as_not_given(auth_client, manager, portal_user, customer, flour):
    request = _order(customer, (flour, 10))
    set_order_transport(request, portal_user, truck="403BJN13", trailer="07KG837PB")

    response = _confirm(auth_client(manager), request, truck_number="", trailer_number="")

    assert response.status_code == 200, response.data
    request.refresh_from_db()
    assert (request.truck_number, request.trailer_number) == ("403BJN13", "07KG837PB")
    assert request.truck_number_set_by == portal_user


def test_confirm_keeps_the_client_number_rule_and_stays_a_request(
    auth_client, manager, portal_user, customer, flour
):
    request = _order(customer, (flour, 10))
    item = request.items.get()
    set_order_transport(request, portal_user, truck="403BJN13")

    response = _confirm(
        auth_client(manager), request, truck_number="777AAA01", quantities={str(item.pk): 8})

    assert response.status_code == 400
    assert response.data["code"] == "forbidden"
    request.refresh_from_db()
    item.refresh_from_db()
    assert (request.status, request.truck_number, item.quantity) == ("pending", "403BJN13", 10)


def test_confirm_takes_a_wagon_number(auth_client, manager, customer, flour):
    request = _order(customer, (flour, 10), transport_type="train")

    bad = _confirm(auth_client(manager), request, truck_number="1234")
    assert bad.status_code == 400
    response = _confirm(auth_client(manager), request, truck_number="12345678")

    assert response.status_code == 200, response.data
    request.refresh_from_db()
    assert (request.truck_number, request.trailer_number) == ("12345678", "")


def test_confirm_accepts_a_multipart_form(auth_client, manager, customer, flour):
    request = _order(customer, (flour, 10))

    response = auth_client(manager).post(
        f"/api/orders/{request.pk}/confirm/", {"department": "main", "truck_number": "403 BJN 13"})

    assert response.status_code == 200, response.data
    request.refresh_from_db()
    assert (request.status, request.truck_number) == ("confirmed", "403BJN13")


def test_confirm_rejects_a_too_long_number(auth_client, manager, customer, flour):
    request = _order(customer, (flour, 10))

    response = _confirm(auth_client(manager), request, truck_number="A" * 31)

    assert response.status_code == 400
    request.refresh_from_db()
    assert request.status == "pending"
