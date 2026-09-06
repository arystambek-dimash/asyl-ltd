from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cameras.models import MonoblockCameraSettings
from apps.clients.models import Client
from apps.orders.models import Order
from apps.shipments.models import Shipment


pytestmark = pytest.mark.django_db


def _order(client, status, shipped_at=None, **fields):
    order = Order.objects.create(client=client, status=status, **fields)
    if shipped_at is not None:
        Shipment.objects.create(order=order, shipped_at=shipped_at)
    return order


def _ids(response) -> set[int]:
    return {item["id"] for item in response.data}


def test_post_board_defaults_to_active_orders_and_todays_completed(auth_client, operator):
    client = Client.objects.create_with_user(first_name="Board", last_name="Client", phone="1")
    active = _order(client, "loading")
    today = _order(client, "shipped", timezone.now())
    old = _order(client, "shipped", timezone.now() - timedelta(days=1))
    _order(client, "pending")

    response = auth_client(operator).get("/api/orders/?post_board=1")

    assert response.status_code == 200
    assert {item["id"] for item in response.data} == {active.id, today.id}
    assert old.id not in {item["id"] for item in response.data}


def test_post_board_uses_admin_completed_days(auth_client, operator):
    MonoblockCameraSettings.objects.create(completed_orders_days=3)
    client = Client.objects.create_with_user(first_name="Board", last_name="History", phone="2")
    recent = _order(client, "shipped", timezone.now() - timedelta(days=2))
    old = _order(client, "shipped", timezone.now() - timedelta(days=3))

    response = auth_client(operator).get("/api/orders/?post_board=1")

    ids = {item["id"] for item in response.data}
    assert recent.id in ids
    assert old.id not in ids


def test_post_board_is_available_to_train_loader(
    auth_client, user_with_perms
):
    loader = user_with_perms(
        "board-train-loader",
        codes=["train.view", "train.load"],
    )
    client = Client.objects.create_with_user(
        first_name="Train", last_name="Loader", phone="3"
    )
    active = _order(client, "confirmed")

    response = auth_client(loader).get("/api/orders/?post_board=1")

    assert response.status_code == 200
    assert {item["id"] for item in response.data} == {active.id}


def test_post_board_shows_todays_waiting_orders_and_all_active_work(auth_client, operator):
    """Очередь «сегодня»: старые подтверждённые заказы не засоряют доску.

    Их находят поиском по номеру или выбором дня; работа на посту (погрузка
    на третий день) видна всегда.
    """

    client = Client.objects.create_with_user(first_name="Board", last_name="Days", phone="5")
    today = timezone.localdate()
    future = _order(client, "confirmed", arrival_date=today + timedelta(days=1))
    overdue = _order(client, "confirmed", arrival_date=today - timedelta(days=2))
    planned_today = _order(client, "confirmed", arrival_date=today)
    unplanned_today = _order(client, "confirmed")
    stale_unplanned = _order(client, "confirmed")
    Order.objects.filter(pk=stale_unplanned.pk).update(
        created_at=timezone.now() - timedelta(days=3)
    )
    long_loading = _order(client, "loading")
    Shipment.objects.create(
        order=long_loading,
        arrived_at=timezone.now() - timedelta(days=3),
        loading_started_at=timezone.now() - timedelta(days=3),
    )

    response = auth_client(operator).get("/api/orders/?post_board=1")

    assert response.status_code == 200
    assert _ids(response) == {planned_today.id, unplanned_today.id, long_loading.id}
    assert {future.id, overdue.id, stale_unplanned.id}.isdisjoint(_ids(response))

    # Старая очередь остаётся доступной: по дню её создания и через поиск.
    stale_day = (today - timedelta(days=3)).isoformat()
    by_day = auth_client(operator).get(f"/api/orders/?post_board=1&day={stale_day}")
    assert stale_unplanned.id in _ids(by_day)
    found = auth_client(operator).get(f"/api/orders/?post_board=1&search={stale_unplanned.id}")
    assert _ids(found) == {stale_unplanned.id}


def test_post_board_explicit_day_shows_that_days_traffic_only(auth_client, operator):
    client = Client.objects.create_with_user(first_name="Board", last_name="Day", phone="6")
    yesterday = timezone.now() - timedelta(days=1)
    shipped_yesterday = _order(client, "shipped", yesterday)
    shipped_today = _order(client, "shipped", timezone.now())
    arrived_yesterday = _order(client, "arrived")
    Shipment.objects.create(order=arrived_yesterday, arrived_at=yesterday)
    arrived_today = _order(client, "arrived")
    Shipment.objects.create(order=arrived_today, arrived_at=timezone.now())
    planned_yesterday = _order(
        client, "confirmed", arrival_date=timezone.localdate() - timedelta(days=1)
    )
    _order(client, "confirmed")

    day = (timezone.localdate() - timedelta(days=1)).isoformat()
    response = auth_client(operator).get(f"/api/orders/?post_board=1&day={day}")

    assert response.status_code == 200
    assert _ids(response) == {
        shipped_yesterday.id, arrived_yesterday.id, planned_yesterday.id
    }
    assert shipped_today.id not in _ids(response)
    assert arrived_today.id not in _ids(response)


def test_post_board_today_as_explicit_day_matches_default(auth_client, operator):
    client = Client.objects.create_with_user(first_name="Board", last_name="Today", phone="7")
    waiting = _order(client, "confirmed")
    long_loading = _order(client, "loading")
    Shipment.objects.create(
        order=long_loading, arrived_at=timezone.now() - timedelta(days=2)
    )

    today = timezone.localdate().isoformat()
    response = auth_client(operator).get(f"/api/orders/?post_board=1&day={today}")

    assert response.status_code == 200
    assert _ids(response) == {waiting.id, long_loading.id}


def test_post_board_search_finds_plate_across_days(auth_client, operator):
    client = Client.objects.create_with_user(first_name="Board", last_name="Search", phone="8")
    today = timezone.localdate()
    old_trip = _order(
        client, "shipped", timezone.now() - timedelta(days=5), truck_number="327ABC01"
    )
    future_trip = _order(
        client, "confirmed", arrival_date=today + timedelta(days=3),
        truck_number="327XYZ02",
    )
    too_old = _order(
        client, "shipped", timezone.now() - timedelta(days=40), truck_number="327OLD03"
    )
    other_plate = _order(client, "confirmed", truck_number="555AAA01")
    _order(client, "pending", truck_number="327PEN04")

    response = auth_client(operator).get("/api/orders/?post_board=1&search=327")

    assert response.status_code == 200
    assert _ids(response) == {old_trip.id, future_trip.id}
    assert too_old.id not in _ids(response)
    assert other_plate.id not in _ids(response)


def test_post_board_search_matches_client_and_order_number(auth_client, operator):
    magnum = Client.objects.create_with_user(
        first_name="Магнум", last_name="Плюс", phone="9"
    )
    other = Client.objects.create_with_user(first_name="Другой", phone="10")
    by_client = _order(magnum, "confirmed", arrival_date=timezone.localdate() + timedelta(days=1))
    by_number = _order(other, "loaded")
    _order(other, "confirmed")

    client = auth_client(operator)
    assert _ids(client.get("/api/orders/?post_board=1&search=Магн")) == {by_client.id}
    assert _ids(client.get(f"/api/orders/?post_board=1&search={by_number.id}")) == {by_number.id}
    assert _ids(client.get("/api/orders/?post_board=1&search=%20%20")) >= {by_number.id}


def test_post_board_search_matches_plate_typed_with_spaces(auth_client, operator):
    """Номер хранится слитно (327XXX17), а оператор набирает «327 XXX 17»."""
    client = Client.objects.create_with_user(first_name="Board", last_name="Plate", phone="11")
    spaced = _order(client, "loading", truck_number="327XXX17")
    _order(client, "loading", truck_number="327YYY17")

    response = auth_client(operator).get(
        "/api/orders/", {"post_board": "1", "search": "327 XXX 17"}
    )

    assert response.status_code == 200
    assert _ids(response) == {spaced.id}


def test_post_board_search_with_unicode_digit_is_not_an_order_number(auth_client, operator):
    """``"²".isdigit()`` истинно, но ``int("²")`` падает — поиск не отдаёт 500."""
    client = Client.objects.create_with_user(first_name="Board", last_name="Digit", phone="12")
    _order(client, "loading", truck_number="327ZZZ17")

    response = auth_client(operator).get("/api/orders/", {"post_board": "1", "search": "²"})

    assert response.status_code == 200
    assert _ids(response) == set()


def test_post_board_rejects_garbage_day(auth_client, operator):
    response = auth_client(operator).get("/api/orders/?post_board=1&day=yesterday")

    assert response.status_code == 400
    assert response.data["code"] == "bad_date"


def test_dashboard_operational_returns_authoritative_data(
    auth_client, operator
):
    from apps.eventlog.models import EventLog

    client = Client.objects.create_with_user(
        first_name="Dashboard", last_name="Operator", phone="4"
    )
    loading = _order(client, "loading")
    _order(client, "pending")
    shipped = _order(client, "shipped", timezone.now())
    EventLog.objects.create(
        event_type="shipment",
        message="Отгружено",
        order=shipped,
        payload={"bags_loaded": 12},
    )
    today = timezone.localdate().isoformat()

    response = auth_client(operator).get(
        f"/api/orders/dashboard-operational/?from={today}&to={today}"
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.data["queue"]] == [loading.id]
    assert response.data["attention"] == {
        "pending_payments": 0,
        "awaiting_review": 1,
        "stuck_in_loading": 1,
    }
    assert response.data["days"] == [
        {"date": today, "bags": 12, "orders": 1}
    ]
