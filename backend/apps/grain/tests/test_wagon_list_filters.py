"""Фильтры списка рейсов: день, поиск и порядок для «разбить по дням».

Завершённые рейсы живут по дате выезда, активные — по дате заезда. Ошибки
периода отдаются теми же кодами, что и в журнале событий, чтобы фронт
обрабатывал их одним способом.
"""

from datetime import datetime, timedelta

import pytest
from apps.grain import statuses as st
from apps.grain.models import GrainSupply, Wagon
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(user_with_perms):
    return user_with_perms("wagon-list-viewer", codes=["grain.view"])


def _at(day: int, hour: int = 12, month: int = 9):
    return timezone.make_aware(datetime(2026, month, day, hour, 0))


def _passage(number, *, status=st.ARRIVED, arrived_at=None, exited_at=None, cargo="Отруби"):
    return Wagon.objects.create(
        number=number,
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name=cargo,
        status=status,
        arrived_at=arrived_at,
        exited_at=exited_at,
    )


def _intake(number, supplier, *, status=st.ARRIVED, arrived_at=None, exited_at=None):
    supply = GrainSupply.objects.create(
        supplier=supplier, culture="Пшеница", status="expected", simple_flow=True
    )
    return Wagon.objects.create(
        supply=supply,
        number=number,
        direction=Wagon.INTAKE,
        workflow="simple",
        status=status,
        arrived_at=arrived_at,
        exited_at=exited_at,
    )


def _ids(response):
    assert response.status_code == 200, response.data
    return [row["id"] for row in response.data]


def test_finished_list_filters_by_exit_day(auth_client, viewer):
    """Заезд был вчера, выезд — сегодня: рейс относится ко дню выезда."""
    today = _passage(
        "111AAA01",
        status=st.COMPLETED,
        arrived_at=_at(4, 20),
        exited_at=_at(5, 9),
    )
    _passage("222BBB02", status=st.COMPLETED, arrived_at=_at(4, 8), exited_at=_at(4, 16))

    response = auth_client(viewer).get(
        "/api/grain/wagons/?scope=finished&direction=passage"
        "&date_from=2026-09-05&date_to=2026-09-05"
    )

    assert _ids(response) == [today.pk]


def test_cancelled_trip_without_exit_lands_on_its_arrival_day(auth_client, viewer):
    """Отменённый рейс не выезжал: его день — заезд, как в таблице фронта."""
    cancelled = _passage("111AAA01", status=st.CANCELLED, arrived_at=_at(4, 10))
    _passage("222BBB02", status=st.COMPLETED, arrived_at=_at(4, 8), exited_at=_at(5, 9))

    response = auth_client(viewer).get(
        "/api/grain/wagons/?scope=finished&direction=passage"
        "&date_from=2026-09-04&date_to=2026-09-04"
    )

    assert _ids(response) == [cancelled.pk]


def test_on_site_list_filters_by_arrival_day(auth_client, viewer):
    yesterday = _passage("111AAA01", arrived_at=_at(4, 22))
    _passage("222BBB02", arrived_at=_at(5, 7))

    response = auth_client(viewer).get(
        "/api/grain/wagons/?scope=on_site&direction=passage"
        "&date_from=2026-09-04&date_to=2026-09-04"
    )

    assert _ids(response) == [yesterday.pk]


@pytest.mark.parametrize(
    "params, code",
    [
        ({"date_from": "not-a-date"}, "bad_date"),
        ({"date_to": "2026-02-31"}, "bad_date"),
        ({"date_from": "2026-09-06", "date_to": "2026-09-01"}, "bad_range"),
    ],
)
def test_invalid_day_filters_return_normalized_400(auth_client, viewer, params, code):
    response = auth_client(viewer).get(
        "/api/grain/wagons/", {"scope": "finished", **params}
    )

    assert response.status_code == 400
    assert response.data["code"] == code


def test_search_matches_number_even_with_spaces(auth_client, viewer):
    match = _passage("465BDS13", arrived_at=_at(5))
    _passage("777CCC02", arrived_at=_at(5))

    exact = auth_client(viewer).get("/api/grain/wagons/?scope=on_site&search=bds13")
    spaced = auth_client(viewer).get(
        "/api/grain/wagons/?scope=on_site&search=465 bds 13"
    )

    assert _ids(exact) == [match.pk]
    assert _ids(spaced) == [match.pk]


def test_search_matches_supplier_and_cargo(auth_client, viewer):
    # Регистр кириллицы сворачивается локалью БД (как и в журнале событий),
    # поэтому здесь ищем в том же регистре, а регистронезависимость — на латинице.
    kolos = _intake("Поезд-1", "ТОО Колос", arrived_at=_at(5))
    _intake("Поезд-2", "АО Нива", arrived_at=_at(5))
    bran = _passage("111AAA01", arrived_at=_at(5), cargo="Отруби")
    _passage("222BBB02", arrived_at=_at(5), cargo="Мучка")

    by_supplier = auth_client(viewer).get("/api/grain/wagons/?scope=on_site&search=Колос")
    by_cargo = auth_client(viewer).get("/api/grain/wagons/?scope=on_site&search=Отруб")

    assert _ids(by_supplier) == [kolos.pk]
    assert _ids(by_cargo) == [bran.pk]


def test_finished_list_orders_by_exit_time_then_id(auth_client, viewer):
    """Порядок создания не совпадает с порядком выезда — сверху свежий выезд.

    Отменённый рейс без выезда встаёт по времени заезда, а не в конец списка.
    """
    late_exit = _passage("111AAA01", status=st.COMPLETED, exited_at=_at(5, 18))
    early_exit = _passage("222BBB02", status=st.COMPLETED, exited_at=_at(5, 9))
    latest_exit = _passage("333CCC03", status=st.COMPLETED, exited_at=_at(5, 21))
    cancelled = _passage(
        "444DDD04", status=st.CANCELLED, arrived_at=_at(5, 12), exited_at=None
    )

    response = auth_client(viewer).get("/api/grain/wagons/?scope=finished&direction=passage")

    assert _ids(response) == [latest_exit.pk, late_exit.pk, cancelled.pk, early_exit.pk]


def test_on_site_list_keeps_newest_record_first(auth_client, viewer):
    older = _passage("111AAA01", arrived_at=_at(5, 18))
    newer = _passage("222BBB02", arrived_at=_at(5, 9))

    response = auth_client(viewer).get("/api/grain/wagons/?scope=on_site&direction=passage")

    assert _ids(response) == [newer.pk, older.pk]


def test_day_filter_uses_the_local_calendar_day(auth_client, viewer):
    """Граничные часы дня входят целиком: сравнение идёт по дате, не по времени."""
    first_minute = _passage("111AAA01", status=st.COMPLETED, exited_at=_at(5, 0))
    last_hour = _passage("222BBB02", status=st.COMPLETED, exited_at=_at(5, 23))
    _passage("333CCC03", status=st.COMPLETED, exited_at=_at(5, 0) + timedelta(days=1))

    response = auth_client(viewer).get(
        "/api/grain/wagons/?scope=finished&date_from=2026-09-05&date_to=2026-09-05"
    )

    assert _ids(response) == [last_hour.pk, first_minute.pk]
