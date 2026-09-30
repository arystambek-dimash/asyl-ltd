"""Автоматический приход по табличке вагона.

Датчика прибытия поезда на территории нет — его роль играет камера. Табличка
в кадре означает, что состав встал под разгрузку; распознанный OCR номер
связывает приезд с заранее заведённой поставкой.

Главный риск здесь — дубли: состав стоит под разгрузкой долго, и каждая
следующая детекция не должна плодить новые рейсы.
"""

import logging
from datetime import timedelta
from http.client import IncompleteRead
from unittest.mock import Mock, patch

import pytest
from django.core import signing
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection
from django.utils import timezone

from apps.cameras import ai, continuous
from apps.cameras.tests.shipping_fakes import recognition_payload, wagon_plate
from apps.grain import statuses as st
from apps.grain.models import Wagon
from apps.grain.photos import SIGNING_SALT, photo_token
from apps.grain.serializers import WagonBriefSerializer, WagonSerializer
from apps.grain.services import (
    AUTO_ARRIVAL_GAP,
    capture_arrival_photo,
    register_detected_arrival,
)

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


@pytest.fixture
def fresh_plate_poll():
    """Опрос таблички начинается с чистого листа: без прошлого времени опроса."""
    cache.delete(continuous.WAGON_PLATE_STATE_KEY)


def test_poll_opens_an_intake_when_the_plate_is_seen(fresh_plate_poll):
    with _settings(), patch.object(
        continuous.ai, "wagon_plate_scan",
        return_value={"seen": True, "number": ""},
    ):
        result = continuous.poll_wagon_plate()

    assert result["created"] is not None
    assert Wagon.objects.filter(number_source="camera").count() == 1


def test_poll_does_nothing_without_a_camera_role(fresh_plate_poll):
    with _settings(camera=""):
        assert continuous.poll_wagon_plate() == {"skipped": "no_camera"}
    assert not Wagon.objects.exists()


def test_poll_treats_an_unreachable_service_as_unknown(fresh_plate_poll):
    """Молчание сервиса — не «поезда нет»: рейсы не трогаем."""
    with _settings(), patch.object(continuous.ai, "wagon_plate_scan", return_value=None):
        result = continuous.poll_wagon_plate()

    assert result == {"seen": None}
    assert not Wagon.objects.exists()


def test_poll_respects_its_own_period(fresh_plate_poll):
    """Цикл мониторинга крутится чаще, чем нужно спрашивать модель."""
    with _settings(), patch.object(
        continuous.ai, "wagon_plate_scan",
        return_value={"seen": False, "number": ""},
    ) as probe:
        continuous.poll_wagon_plate()
        second = continuous.poll_wagon_plate()

    assert second == {"skipped": "too_soon"}
    assert probe.call_count == 1

# ── Номер из OCR ──────────────────────────────────────────────────────────


def _supply_with_expected_wagon(number="12345678"):
    """Диспетчер завёл приход заранее: поставка и рейс уже ждут вагон."""
    from apps.grain.models import GrainSupply
    from apps.grain.tests.factories import silo_route

    grain_type, silo = silo_route()
    supply = GrainSupply.objects.create(
        supplier="ТОО Колос", grain_type=grain_type,
        assigned_silo=silo, expected_total_kg=60_000, status="expected",
    )
    wagon = Wagon.objects.create(
        supply=supply, number=number, direction=Wagon.INTAKE,
        workflow="simple", status=st.EXPECTED, assigned_silo=silo,
    )
    return supply, wagon


def test_a_recognised_number_takes_the_expected_trip():
    """Главное ради чего OCR: приезд ложится на заказ, а не рядом с ним."""
    supply, expected = _supply_with_expected_wagon("12345678")

    wagon = register_detected_arrival(camera_source="cam3", number="12345678")

    assert wagon is not None and wagon.pk == expected.pk
    assert wagon.status == st.ARRIVED
    assert wagon.supply_id == supply.pk, "рейс связан с поставкой диспетчера"
    assert wagon.number_source == "camera"
    assert Wagon.objects.count() == 1, "безымянный дубль рядом не создан"


def test_an_unknown_number_still_opens_a_trip():
    """Вагона нет в плане — приезд фиксируем, разберётся оператор."""
    wagon = register_detected_arrival(camera_source="cam3", number="99999999")

    assert wagon is not None
    assert wagon.number == "99999999"
    assert wagon.supply_id is None


def test_the_same_wagon_is_not_admitted_twice():
    """Табличка того же вагона в следующем кадре — не второй приезд."""
    _supply_with_expected_wagon("12345678")
    first = register_detected_arrival(camera_source="cam3", number="12345678")

    assert register_detected_arrival(camera_source="cam3", number="12345678") is None
    assert Wagon.objects.count() == 1
    assert Wagon.objects.get().pk == first.pk


def test_poll_passes_the_recognised_number_through(fresh_plate_poll):
    _supply_with_expected_wagon("12345678")
    with _settings(), patch.object(
        continuous.ai, "wagon_plate_scan",
        return_value={"seen": True, "number": "12345678"},
    ):
        result = continuous.poll_wagon_plate()

    assert result["number"] == "12345678"
    assert Wagon.objects.get(pk=result["created"]).number == "12345678"


def wagon_reply(*plates, ocr=True):
    """Ответ /wagon-number/detect в форме cv-service (plate_recognition.py).

    Каждая табличка — ``(digits, accepted, length_valid, checksum_valid)``.
    Номер живёт в ``ocr.digits``; верхний ``number`` — лишь первый принятый.
    """
    if not ocr:
        return {
            "ok": True, "ocr": False, "task": "wagon_plate_detection",
            "detections": [{"class_name": "wagon_plate", "confidence": 0.91} for _ in plates],
        }
    return recognition_payload("wagon_number", [
        wagon_plate(digits, accepted=accepted, length_valid=length_valid, checksum_valid=checksum_valid)
        for digits, accepted, length_valid, checksum_valid in plates
    ])


def _scan(payload):
    frame = b"\xff\xd8\xffjpeg"
    with patch.object(ai, "camera_frame_jpeg", return_value=frame), \
            patch.object(ai, "_request", return_value=(200, payload)):
        result = ai.wagon_plate_scan("cam8main")
    # Кадр скана возвращается целиком — запасное фото прибытия без второго запроса.
    assert result.pop("frame") == frame
    return result


def test_a_valid_number_reaches_the_ledger():
    assert _scan(wagon_reply(("28055531", True, True, True))) == {
        "seen": True, "number": "28055531",
    }


@pytest.mark.parametrize("plate", [
    ("28055532", True, True, False),   # уверенно прочитан, контроль не сошёлся
    ("2805553", True, False, None),    # цифра потерялась
    ("280555311", True, False, None),  # лишняя цифра
    ("28055531", False, True, True),   # сама модель не уверена
])
def test_an_unverified_number_opens_a_trip_without_a_number(plate):
    """Чужой вагон в учёте хуже пустого поля: номер заполнит оператор."""
    assert _scan(wagon_reply(plate)) == {"seen": True, "number": ""}


def test_two_different_numbers_in_one_frame_are_not_guessed():
    reply = wagon_reply(("28055531", True, True, True), ("00123455", True, True, True))

    assert _scan(reply) == {"seen": True, "number": ""}


def test_a_plate_without_ocr_still_opens_a_trip():
    """OCR на ПК выключен: табличка видна — приход заводится без номера."""
    assert _scan(wagon_reply(("", False, False, None), ocr=False)) == {
        "seen": True, "number": "",
    }
    assert _scan(wagon_reply(ocr=False)) == {"seen": False, "number": ""}


def test_a_reply_outside_the_contract_keeps_the_plate_but_drops_the_number():
    reply = wagon_reply(("28055531", True, True, True))
    reply["detections"][0]["ocr"]["digits"] = "00123455"

    assert _scan(reply) == {"seen": True, "number": ""}


# ── Фото прибытия ─────────────────────────────────────────────────────────
# Номер по-прежнему читает только OCR камеры (без GPT); к открытому рейсу
# прикладывается снимок состава — основной поток камеры, без него кадр скана.

MAIN = b"\xff\xd8\xff\xe0main stream frame"
SCAN = b"\xff\xd8\xff\xe0plate scan frame"


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def _poll(main, *, number="", scan_frame=SCAN):
    """Опрос, на котором табличка видна; ``main`` — мок основного потока."""
    cache.delete(continuous.WAGON_PLATE_STATE_KEY)
    with _settings(), patch.object(
        ai, "wagon_plate_scan",
        return_value={"seen": True, "number": number, "frame": scan_frame},
    ), patch.object(ai, "camera_main_frame_jpeg", main):
        return continuous.poll_wagon_plate()


def test_a_camera_arrival_gets_a_main_stream_photo(media):
    main = Mock(return_value=MAIN)

    result = _poll(main)

    wagon = Wagon.objects.get(pk=result["created"])
    main.assert_called_once_with("cam3")
    assert wagon.arrival_photo.name == f"grain/arrivals/{wagon.pk}/arrival.jpg"
    assert wagon.arrival_photo.read() == MAIN
    assert wagon.arrival_photo_taken_at is not None


@pytest.mark.parametrize("main", [
    {"return_value": None},                                  # go2rtc не отдал кадр
    {"side_effect": IncompleteRead(b"")},                    # оборванный ответ
    {"side_effect": ai.AiError(400, "Неизвестная камера")},  # кривое имя камеры
])
def test_without_a_main_frame_the_scan_frame_becomes_the_photo(media, main):
    result = _poll(Mock(**main))

    assert Wagon.objects.get(pk=result["created"]).arrival_photo.read() == SCAN


def test_without_any_frame_the_trip_still_opens(media, caplog):
    with caplog.at_level(logging.WARNING, logger="apps.grain.services"):
        result = _poll(Mock(return_value=None), scan_frame=None)

    wagon = Wagon.objects.get(pk=result["created"])
    assert wagon.status == st.ARRIVED
    assert not wagon.arrival_photo
    assert wagon.arrival_photo_taken_at is None
    assert sum("без фото прибытия" in r.getMessage() for r in caplog.records) == 1
    assert not list(media.rglob("*.jpg"))


def test_a_repeated_sighting_takes_no_second_photo(media):
    main = Mock(return_value=MAIN)
    first = _poll(main)

    second = _poll(main)

    assert second["created"] is None
    assert main.call_count == 1
    assert len(list(media.rglob("*.jpg"))) == 1
    assert Wagon.objects.get(pk=first["created"]).arrival_photo.read() == MAIN


def test_a_claimed_expected_wagon_gets_the_photo(media):
    _, expected = _supply_with_expected_wagon("12345678")

    result = _poll(Mock(return_value=MAIN), number="12345678")

    assert result["created"] == expected.pk
    expected.refresh_from_db()
    assert expected.arrival_photo.read() == MAIN


def test_an_existing_arrival_photo_is_never_overwritten(media):
    wagon = register_detected_arrival(camera_source="cam3")
    wagon.arrival_photo.save("arrival.jpg", ContentFile(SCAN), save=True)
    main = Mock(return_value=MAIN)

    with patch.object(ai, "camera_main_frame_jpeg", main):
        assert capture_arrival_photo(wagon.pk, camera="cam3") is False

    main.assert_not_called()
    wagon.refresh_from_db()
    assert wagon.arrival_photo.read() == SCAN


@pytest.mark.parametrize("meanwhile", ["deleted", "photographed"])
def test_a_trip_changed_during_the_camera_request_wins(media, meanwhile):
    """Рейс удалили или сфотографировали, пока ждали камеру: наш файл лишний."""
    wagon = register_detected_arrival(camera_source="cam3")
    rows = Wagon.objects.filter(pk=wagon.pk)

    def frame(camera):
        if meanwhile == "deleted":
            rows.delete()
        else:
            rows.update(arrival_photo="grain/arrivals/other.jpg")
        return MAIN

    with patch.object(ai, "camera_main_frame_jpeg", side_effect=frame):
        assert capture_arrival_photo(wagon.pk, camera="cam3") is False

    assert not list(media.rglob("*.jpg"))
    if meanwhile == "photographed":
        assert rows.get().arrival_photo.name == "grain/arrivals/other.jpg"


@pytest.mark.django_db(transaction=True)
def test_the_camera_is_asked_only_after_the_trip_is_committed(media):
    """Запрос к камере (до 4 с) не держит транзакцию и блокировки регистрации."""
    inside = []

    def frame(camera):
        inside.append(connection.in_atomic_block)
        return MAIN

    result = _poll(Mock(side_effect=frame))

    assert inside == [False]
    assert Wagon.objects.get(pk=result["created"]).arrival_photo.read() == MAIN


def test_the_trip_card_shows_the_arrival_photo_by_a_signed_link(
    media, api_client, auth_client, user_with_perms
):
    result = _poll(Mock(return_value=MAIN))
    viewer = auth_client(user_with_perms("arrival-photo", codes=["grain.view"]))

    detail = viewer.get(f"/api/grain/wagons/{result['created']}/").data

    url = detail["arrival_photo_url"]
    assert url.startswith(f"/api/grain/photos/arrival/{result['created']}/?token=")
    assert detail["arrival_photo_taken_at"]
    photo = api_client.get(url)
    assert photo.status_code == 200
    assert photo["Content-Type"] == "image/jpeg"
    assert b"".join(photo.streaming_content) == MAIN
    row = viewer.get("/api/grain/wagons/").data[0]
    assert "arrival_photo_url" not in row, "подпись ссылки — только в карточке"


def test_a_trip_without_a_photo_has_no_link():
    wagon = register_detected_arrival(camera_source="cam3")

    data = WagonSerializer(wagon).data

    assert data["arrival_photo_url"] is None
    assert data["arrival_photo_taken_at"] is None
    assert "arrival_photo_taken_at" not in WagonBriefSerializer(wagon).data


def test_the_arrival_photo_link_rejects_bad_tokens(media, api_client):
    wagon = register_detected_arrival(camera_source="cam3")
    base = f"/api/grain/photos/arrival/{wagon.pk}/?token="
    good = photo_token("arrival", wagon.pk)
    assert api_client.get(base + good).status_code == 404, "фото ещё нет"
    wagon.arrival_photo.save("arrival.jpg", ContentFile(MAIN), save=True)

    assert api_client.get(base + "bad").status_code == 404
    assert api_client.get(base + photo_token("weighing", wagon.pk)).status_code == 404
    other = f"/api/grain/photos/arrival/{wagon.pk + 1}/?token={good}"
    assert api_client.get(other).status_code == 404
    unknown = signing.dumps({"k": "nope", "id": wagon.pk}, salt=SIGNING_SALT, compress=True)
    assert api_client.get(f"/api/grain/photos/nope/{wagon.pk}/?token={unknown}").status_code == 404
    response = api_client.get(base + good)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == MAIN
