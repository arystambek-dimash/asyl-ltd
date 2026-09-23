"""API номеров тягача и прицепа: форма заказа, быстрый ввод «Фуры», поиск."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderItem
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def product():
    item = Product.objects.create(name="Мука", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=item, bags=10_000)
    return item


def _client(name="Азамат", **fields):
    return Client.objects.create_with_user(first_name=name, last_name="К", phone=name, **fields)


def _order(client, product, *, quantity=1360, **fields):
    fields.setdefault("status", "confirmed")
    order = Order.objects.create(client=client, **fields)
    OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price="10.00")
    return order


# ── Форма заказа ─────────────────────────────────────────────────────────────


def test_create_normalizes_pair_and_records_its_owner(auth_client, manager, product):
    client = _client()
    response = auth_client(manager).post("/api/orders/", {
        "client": client.pk, "truck_number": "07 kg 695 adt", "trailer_number": "07-KG-837-PB",
        "items": [{"product": product.pk, "quantity": 5}],
    }, format="json")

    assert response.status_code == 201, response.data
    assert (response.data["truck_number"], response.data["trailer_number"]) == ("07KG695ADT", "07KG837PB")
    order = Order.objects.get(pk=response.data["id"])
    assert order.truck_number_set_by == manager


def test_create_rejects_an_impossible_trailer(auth_client, manager, product):
    response = auth_client(manager).post("/api/orders/", {
        "client": _client().pk, "truck_number": "403BJN13", "trailer_number": "AB",
        "items": [{"product": product.pk, "quantity": 5}],
    }, format="json")

    assert response.status_code == 400
    assert "trailer_number" in response.data["detail"]


def test_patch_trailer_keeps_truck_and_notifies_client(auth_client, manager, product):
    order = _order(_client(), product, truck_number="403BJN13")

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/", {"trailer_number": "07kg837pb"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("403BJN13", "07KG837PB")
    assert Notification.objects.filter(
        client=order.client, text=f"Заказ №{order.pk}: машина 403 BJN 13, прицеп 07 KG 837 PB").exists()


def test_patch_trailer_on_site_does_not_tell_the_client(auth_client, manager, product):
    """Карточка заказа: прицеп дописан, когда машина уже на территории, —
    номер клиенту не показывается, и уведомления о нём нет."""
    order = _order(_client(), product, status="arrived", truck_number="403BJN13", truck_number_set_by=manager)

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/", {"trailer_number": "07kg837pb"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("403BJN13", "07KG837PB")
    assert not Notification.objects.filter(client=order.client).exists()


def test_patch_to_train_drops_the_trailer(auth_client, manager, product):
    order = _order(_client(), product, truck_number="", trailer_number="07KG837PB")

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/", {"transport_type": "train", "truck_number": "00123456"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.transport_type, order.truck_number, order.trailer_number) == ("train", "00123456", "")


def test_repeat_copies_the_pair(auth_client, manager, product):
    source = _order(_client(), product, status="shipped", truck_number="07KG695ADT", trailer_number="07KG837PB")

    response = auth_client(manager).post(f"/api/orders/{source.pk}/repeat/", format="json")

    assert response.status_code == 201, response.data
    assert (response.data["truck_number"], response.data["trailer_number"]) == ("07KG695ADT", "07KG837PB")


# ── Быстрый ввод «Фуры» ──────────────────────────────────────────────────────


def test_transport_endpoint_saves_pair_and_returns_light_row(auth_client, manager, product):
    client = _client(country="Кыргызстан")
    order = _order(client, product)

    response = auth_client(manager).post(
        f"/api/orders/{order.pk}/transport/",
        {"truck_number": "07 KG 695 ADT", "trailer_number": "07 KG 837 PB"},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["id"] == order.pk
    assert (response.data["truck_number"], response.data["trailer_number"]) == ("07KG695ADT", "07KG837PB")
    assert response.data["client_country"] == "Кыргызстан"
    assert response.data["bags"] == 1360
    assert response.data["plate_warning"] is None
    assert response.data["transport_locked"] is False
    assert "payments" not in response.data


def test_transport_endpoint_warns_but_saves_an_unusual_plate(auth_client, manager, product):
    order = _order(_client(), product)

    response = auth_client(manager).post(
        f"/api/orders/{order.pk}/transport/", {"truck_number": "CLIENT777"}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["truck_number"] == "CLIENT777"
    assert response.data["plate_warning"]


def test_transport_endpoint_does_not_notify_client(auth_client, manager, product):
    """«Фуры» вбивают номера пачкой — клиенту пишет только отгрузка, не каждое сохранение."""
    order = _order(_client(), product)

    response = auth_client(manager).post(
        f"/api/orders/{order.pk}/transport/",
        {"truck_number": "07KG695ADT", "trailer_number": "07KG837PB"},
        format="json",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("07KG695ADT", "07KG837PB")
    assert not Notification.objects.filter(client=order.client).exists()


def test_transport_endpoint_errors_are_explicit(auth_client, manager, product, make_user):
    portal_user = make_user(username="cli", client=True)
    client = _client(user=portal_user)
    order = _order(client, product, truck_number="403BJN13", truck_number_set_by=portal_user)
    api = auth_client(manager)

    owned = api.post(f"/api/orders/{order.pk}/transport/", {"truck_number": "612BEX13"}, format="json")
    impossible = api.post(f"/api/orders/{order.pk}/transport/", {"truck_number": "403BJN13", "trailer_number": "A" * 31}, format="json")

    assert owned.status_code == 400
    assert owned.data["code"] == "forbidden"
    assert impossible.status_code == 400
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("403BJN13", "")


@pytest.mark.parametrize("status", ["shipped", "cancelled", "rejected"])
def test_transport_endpoint_is_only_for_orders_in_work(auth_client, manager, product, status):
    """Отгруженный или закрытый заказ быстрый ввод не трогает и клиенту не пишет."""
    order = _order(_client(), product, status=status)

    response = auth_client(manager).post(
        f"/api/orders/{order.pk}/transport/", {"truck_number": "403BJN13"}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "invalid_status"
    order.refresh_from_db()
    assert order.truck_number == ""
    assert not Notification.objects.filter(client=order.client).exists()


def test_transport_endpoints_require_order_edit(auth_client, user_with_perms, product):
    viewer = user_with_perms("viewer", codes=["orders.view"])
    order = _order(_client(), product)
    api = auth_client(viewer)

    assert api.get("/api/orders/transport-queue/").status_code == 403
    assert api.post(f"/api/orders/{order.pk}/transport/", {"truck_number": "403BJN13"}, format="json").status_code == 403


def test_transport_queue_lists_trucks_waiting_for_a_number(auth_client, manager, product):
    today = timezone.localdate()
    client = _client()
    missing_old = _order(client, product, arrival_date=today - timedelta(days=2))
    missing_today = _order(client, product, arrival_date=today)
    numbered_today = _order(client, product, arrival_date=today, truck_number="403BJN13")
    _order(client, product, arrival_date=today, transport_type="train")
    _order(client, product, arrival_date=today, status="pending")
    _order(client, product, arrival_date=today, status="arrived")
    api = auth_client(manager)

    default = api.get("/api/orders/transport-queue/")
    today_rows = api.get("/api/orders/transport-queue/?filter=today")

    assert default.status_code == 200
    assert [row["id"] for row in default.data] == [missing_old.pk, missing_today.pk]
    assert [row["id"] for row in today_rows.data] == [missing_today.pk, numbered_today.pk]
    assert api.get("/api/orders/transport-queue/?filter=bogus").status_code == 400


def test_transport_queue_suggests_the_clients_last_pairs(auth_client, manager, product):
    client = _client()
    other = _client("Берик")
    _order(client, product, status="shipped", truck_number="111AAA01")
    _order(client, product, status="shipped", truck_number="07KG695ADT", trailer_number="07KG837PB")
    _order(client, product, status="shipped", truck_number="222BBB02")
    _order(client, product, status="shipped", truck_number="07 kg 695 adt", trailer_number="07kg837pb")
    _order(client, product, status="shipped", truck_number="333CCC03")
    _order(client, product, status="shipped", truck_number="самовывоз")
    _order(other, product, status="shipped", truck_number="999ZZZ09")
    waiting = _order(client, product)

    row = next(r for r in auth_client(manager).get("/api/orders/transport-queue/").data if r["id"] == waiting.pk)

    # Новые сначала, одна машина в разной записи — одна подсказка (слитно),
    # не больше трёх, свободный текст вместо номера не подсказывается.
    assert row["transport_suggestions"] == [
        {"truck_number": "333CCC03", "trailer_number": ""},
        {"truck_number": "07KG695ADT", "trailer_number": "07KG837PB"},
        {"truck_number": "222BBB02", "trailer_number": ""},
    ]


def test_transport_queue_query_count_does_not_grow_with_rows(
    auth_client, manager, product, django_assert_max_num_queries,
):
    def fill(count):
        for index in range(count):
            client = _client(f"Клиент{Order.objects.count()}-{index}")
            _order(client, product, status="shipped", truck_number=f"{100 + index}ABC01")
            _order(client, product)

    api = auth_client(manager)
    fill(2)
    with CaptureQueriesContext(connection) as small:
        assert len(api.get("/api/orders/transport-queue/").data) == 2
    fill(6)
    with django_assert_max_num_queries(len(small.captured_queries)):
        assert len(api.get("/api/orders/transport-queue/").data) == 8


def test_transport_queue_today_reads_number_owners_without_n_plus_one(
    auth_client, manager, product, make_user, django_assert_max_num_queries,
):
    """«Все на сегодня» — строки с номером: transport_locked читает владельца номера."""
    staff = make_user(username="dispatcher")

    def fill(count):
        for index in range(count):
            client = _client(f"Клиент{Order.objects.count()}-{index}")
            _order(client, product, truck_number=f"{100 + index}ABC01", truck_number_set_by=staff)
            _order(client, product, truck_number=f"{200 + index}ABC01", truck_number_set_by=client.user)

    api = auth_client(manager)
    fill(1)
    with CaptureQueriesContext(connection) as small:
        rows = api.get("/api/orders/transport-queue/?filter=today").data
    assert [row["transport_locked"] for row in rows] == [False, True]
    fill(4)
    with django_assert_max_num_queries(len(small.captured_queries)):
        assert len(api.get("/api/orders/transport-queue/?filter=today").data) == 10


# ── Поиск ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("search", ["07KG695", "07 695 adt", "07695ADT", "07695", "07 695", "837pb", "07 KG 837"])
def test_order_search_finds_truck_and_trailer_in_any_spelling(auth_client, manager, product, search):
    found = _order(_client(), product, truck_number="07KG695ADT", trailer_number="07KG837PB")
    _order(_client("Берик"), product, truck_number="403BJN13")

    rows = auth_client(manager).get("/api/orders/", {"search": search}).data

    assert [row["id"] for row in rows] == [found.pk]
