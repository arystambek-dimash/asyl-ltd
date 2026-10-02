"""«Скопировать отчёт» в истории фур у грузчика: GET /api/loader/truck-report/."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.bots.tests.samples import train_order, truck_order
from apps.orders.backdate import backdate_moment
from apps.orders.models import Order
from apps.sales.models import Department
from apps.shipments import views as loader_views

pytestmark = pytest.mark.django_db

REPORT = "/api/loader/truck-report/"


def _shipped(client, product, *, bags=20, truck="909ERD13", day=None, hour=12):
    """Фура, отгруженная сегодня (или в ``day``) в ``hour`` часов."""
    at = backdate_moment(day or timezone.localdate()).replace(hour=hour)
    return truck_order(client, (product, bags), truck=truck, shipped_at=at)


@pytest.fixture
def trucks_viewer(user_with_perms):
    """Видит вкладку «Фуры» грузчика, отгружать не может."""
    return user_with_perms("trucks-viewer", codes=["loader.view", "loader.trucks"])


@pytest.fixture
def api(auth_client, trucks_viewer):
    return auth_client(trucks_viewer)


def test_one_order_is_reported_in_the_chat_format(api, nurzhan, product):
    order = _shipped(nurzhan, product, bags=450)
    day = timezone.localdate()

    response = api.get(REPORT, {"order": order.pk})

    assert response.status_code == 200, response.data
    assert response.data == {
        "text": f"Отгрузка {day:%d.%m.%y}\nkz 909 ERD 13\nНуржан Сарыагаш 87029368080\nД1с(50кг)- 22,5 тн\nСклад: Основной склад",
        "order_ids": [order.pk],
    }


def test_history_filters_select_the_period_in_shipment_time_order(api, client, nurzhan, product):
    today = timezone.localdate()
    late = _shipped(nurzhan, product, truck="403BJN13", hour=15)
    early = _shipped(nurzhan, product, truck="909ERD13", hour=9)
    old = _shipped(nurzhan, product, truck="160AL17", day=today - timedelta(days=2))
    train_order(client, product, shipped_at=backdate_moment(today))
    waiting = _shipped(nurzhan, product, truck="111AAA01")
    Order.objects.filter(pk=waiting.pk).update(status="confirmed")

    data = api.get(REPORT).data

    assert data["order_ids"] == [early.pk, late.pk]
    assert [block.splitlines()[1] for block in data["text"].split("\n\n")] == ["kz 909 ERD 13", "kz 403 BJN 13"]
    two_days_ago = (today - timedelta(days=2)).isoformat()
    period = api.get(REPORT, {"date_from": two_days_ago, "date_to": today.isoformat()}).data
    assert period["order_ids"] == [old.pk, early.pk, late.pk]
    assert api.get(REPORT, {"search": "403 bjn"}).data["order_ids"] == [late.pk]


def test_empty_period_is_an_empty_report(api):
    assert api.get(REPORT).data == {"text": "", "order_ids": []}


def test_only_shipped_trucks_of_the_area_and_department(api, auth_client, user_with_perms, client, nurzhan, product):
    wagon = train_order(client, product, shipped_at=backdate_moment(timezone.localdate()))
    waiting = _shipped(nurzhan, product)
    Order.objects.filter(pk=waiting.pk).update(status="confirmed")

    assert api.get(REPORT, {"order": wagon.pk}).status_code == 404
    assert api.get(REPORT, {"order": waiting.pk}).status_code == 404

    retail = Department.objects.create(code="retail", name="Розница")
    stranger = auth_client(user_with_perms("retail", codes=["loader.view", "loader.trucks"], department=retail))
    order = _shipped(nurzhan, product)
    assert stranger.get(REPORT, {"order": order.pk}).status_code == 404
    assert stranger.get(REPORT).data["order_ids"] == []


def test_wagons_only_loader_gets_no_truck_report(auth_client, user_with_perms, nurzhan, product):
    wagons = auth_client(user_with_perms("wagons", codes=["loader.view", "loader.confirm", "loader.wagons"]))
    order = _shipped(nurzhan, product)

    assert wagons.get(REPORT).status_code == 403
    assert wagons.get(REPORT, {"order": order.pk}).status_code == 403


def test_without_loader_view_there_is_no_report(auth_client, user_with_perms, nurzhan, product):
    outsider = auth_client(user_with_perms("outsider", codes=["loader.trucks"]))

    assert outsider.get(REPORT, {"order": _shipped(nurzhan, product).pk}).status_code == 403


def test_trash_is_not_reported(api, nurzhan, product):
    kept = _shipped(nurzhan, product, hour=9)
    trashed = _shipped(nurzhan, product, hour=10)
    Order.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())

    assert api.get(REPORT, {"order": trashed.pk}).status_code == 404
    assert api.get(REPORT).data["order_ids"] == [kept.pk]


def test_too_many_orders_for_the_period_is_refused(api, nurzhan, product, monkeypatch):
    monkeypatch.setattr(loader_views, "REPORT_MAX_ORDERS", 2)
    for hour in (9, 10, 11):
        _shipped(nurzhan, product, hour=hour)

    response = api.get(REPORT)

    assert response.status_code == 400
    assert response.data["code"] == "report_too_many_orders"


def test_bad_order_parameter_is_refused(api):
    assert api.get(REPORT, {"order": "abc"}).status_code == 400


def test_queries_do_not_grow_with_the_period(api, nurzhan, product, django_assert_max_num_queries):
    _shipped(nurzhan, product, hour=8)
    with CaptureQueriesContext(connection) as small:
        assert len(api.get(REPORT).data["order_ids"]) == 1
    for hour in range(9, 15):
        _shipped(nurzhan, product, hour=hour)

    with django_assert_max_num_queries(len(small.captured_queries)):
        assert len(api.get(REPORT).data["order_ids"]) == 7
