"""Автоматический приход по табличке вагона.

Датчика прибытия поезда на территории нет — его роль играет камера. Модель
находит табличку, но цифры не читает, поэтому рейс заводится без номера:
важны факт и время заезда, а номер допишет оператор или будущий OCR.

Главный риск здесь — дубли: состав стоит под разгрузкой долго, и каждая
следующая детекция не должна плодить новые рейсы.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cameras import continuous
from apps.grain import statuses as st
from apps.grain.models import Wagon
from apps.grain.services import AUTO_ARRIVAL_GAP, register_detected_arrival

pytestmark = pytest.mark.django_db


def test_plate_in_frame_opens_an_intake_without_a_number():
    wagon = register_detected_arrival(camera_source="cam3")

    assert wagon is not None
    assert wagon.direction == Wagon.INTAKE
    assert wagon.status == st.ARRIVED
    assert wagon.number == "", "номер даст OCR или оператор — модель его не читает"
    assert wagon.number_source == "camera"
    assert wagon.number_camera_source == "cam3"
    assert wagon.arrived_at is not None


def test_the_same_train_does_not_open_a_second_trip():
    """Табличка видна минуту за минутой — это один состав, а не десять."""
    first = register_detected_arrival(camera_source="cam3")

    assert register_detected_arrival(camera_source="cam3") is None
    assert Wagon.objects.count() == 1
    assert Wagon.objects.get().pk == first.pk


def test_a_new_train_opens_after_a_gap_without_plates():
    """Состав уехал, приход закрыт, спустя паузу приехал следующий."""
    first = register_detected_arrival(camera_source="cam3")
    Wagon.objects.filter(pk=first.pk).update(
        status=st.COMPLETED,
        arrived_at=timezone.now() - AUTO_ARRIVAL_GAP - timedelta(minutes=1),
    )

    second = register_detected_arrival(camera_source="cam3")

    assert second is not None and second.pk != first.pk
    assert Wagon.objects.count() == 2


def test_a_finished_trip_still_blocks_a_new_one_inside_the_gap():
    """Поезд уехал только что — следующая табличка ещё его собственная."""
    first = register_detected_arrival(camera_source="cam3")
    Wagon.objects.filter(pk=first.pk).update(status=st.COMPLETED)

    assert register_detected_arrival(camera_source="cam3") is None


def test_an_open_trip_blocks_a_new_one_even_after_the_gap():
    """Состав стоит под разгрузкой дольше паузы — это по-прежнему он."""
    first = register_detected_arrival(camera_source="cam3")
    Wagon.objects.filter(pk=first.pk).update(
        arrived_at=timezone.now() - AUTO_ARRIVAL_GAP - timedelta(hours=2),
    )

    assert register_detected_arrival(camera_source="cam3") is None


def test_a_manual_wagon_does_not_block_the_camera():
    """Ручной рейс живёт своей жизнью: камера открывает приход независимо."""
    Wagon.objects.create(
        number="12345678", direction=Wagon.INTAKE, status=st.ARRIVED,
        number_source="manual", arrived_at=timezone.now(),
    )

    assert register_detected_arrival(camera_source="cam3") is not None


# ── Периодический опрос камеры ────────────────────────────────────────────


def _settings(camera="cam3"):
    from apps.cameras.models import MonoblockCameraSettings

    return patch.object(
        MonoblockCameraSettings, "wagon_number_source",
        staticmethod(lambda: camera),
    )


def test_poll_opens_an_intake_when_the_plate_is_seen():
    from django.core.cache import cache

    cache.delete(continuous.WAGON_PLATE_STATE_KEY)
    with _settings(), patch.object(continuous.ai, "wagon_plate_seen", return_value=True):
        result = continuous.poll_wagon_plate()

    assert result["created"] is not None
    assert Wagon.objects.filter(number_source="camera").count() == 1


def test_poll_does_nothing_without_a_camera_role():
    from django.core.cache import cache

    cache.delete(continuous.WAGON_PLATE_STATE_KEY)
    with _settings(camera=""):
        assert continuous.poll_wagon_plate() == {"skipped": "no_camera"}
    assert not Wagon.objects.exists()


def test_poll_treats_an_unreachable_service_as_unknown():
    """Молчание сервиса — не «поезда нет»: рейсы не трогаем."""
    from django.core.cache import cache

    cache.delete(continuous.WAGON_PLATE_STATE_KEY)
    with _settings(), patch.object(continuous.ai, "wagon_plate_seen", return_value=None):
        result = continuous.poll_wagon_plate()

    assert result == {"seen": None}
    assert not Wagon.objects.exists()


def test_poll_respects_its_own_period():
    """Цикл мониторинга крутится чаще, чем нужно спрашивать модель."""
    from django.core.cache import cache

    cache.delete(continuous.WAGON_PLATE_STATE_KEY)
    with _settings(), patch.object(
        continuous.ai, "wagon_plate_seen", return_value=False,
    ) as probe:
        continuous.poll_wagon_plate()
        second = continuous.poll_wagon_plate()

    assert second == {"skipped": "too_soon"}
    assert probe.call_count == 1
