"""Общие фабрики тестов зерна: автовесы, сборщик, номер с камеры, рейс вывоза, снимки."""
import time as real_time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.cameras.models import VehiclePlateEvent
from apps.grain import scale
from apps.grain import statuses as st
from apps.grain import weighing_identity as identity
from apps.grain.models import (
    Silo,
    SiloType,
    UnassignedWeighing,
    VehicleOrientationSample,
    Wagon,
    WeighingRecord,
)
from weighbridge.collector import Collector
from weighbridge.outbox import Outbox

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 32


def scale_reading(weight, *, age="0.2", updated_at="2026-08-12T10:00:00Z"):
    """Стабильный вес, как его отдаёт ``scale.read_truck_scale``."""
    return scale.ScaleReading(weight_kg=Decimal(weight), age_seconds=Decimal(age), updated_at=updated_at)


def scale_observation(weight, second, *, stable=True, state="ready"):
    """Показание весов для сборщика; ``second`` задаёт метку показания."""
    return scale.ScaleObservation(
        state, None if weight is None else Decimal(weight), True, stable and state == "ready", False,
        Decimal("0.1"), f"t{second}",
    )


def fake_time(clock):
    """Модуль ``time`` сборщика, у которого монотонные часы — ``clock["now"]``."""
    return SimpleNamespace(monotonic=lambda: clock["now"], time=real_time.time, sleep=real_time.sleep)


def outbox_event(**overrides):
    """Взвешивание, которое сборщик кладёт в outbox."""
    return {
        "id": str(uuid4()), "version": 1, "weight_kg": 4200, "camera": "cam1",
        "stable_weight_at": timezone.now().isoformat(), "scale_age_seconds": "0.1", "scale_updated_at": "sample",
        **overrides,
    }


def collector_event(path, **overrides):
    """Сборщик, у которого машина из ``outbox_event`` ещё стоит на весах."""
    box, value = Outbox(path), outbox_event(**overrides)
    box.put(value)
    collector = Collector(box)
    collector.current = value["id"]
    collector.last_good = real_time.monotonic()
    return collector, box, value


def recognized(_camera, request_id, stable_weight_at, *, number="123ABC02", orientation=None):
    """Ответ ПК камер «номер подтверждён» — подставляется вместо ``recognize_vehicle_from_camera``."""
    reply = {
        "ok": True,
        "status": "recognized",
        "request_id": str(request_id),
        "camera": "cam1",
        "source": "main",
        "stable_weight_at": stable_weight_at,
        "recognized_at": timezone.now().isoformat(),
        "vehicle_number": number,
        "confirmation": {"votes": 3, "detector_confidence": 0.91, "ocr_confidence": 0.96},
    }
    if orientation:
        reply["orientation"] = {"label": orientation, "confidence": 0.99}
    return reply


def process_without_gpt():
    """Проход сверки номеров, который обязан обойтись без запроса к ИИ."""
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()


def vehicle_plate_event(*, received_at=None, **fields):
    """Номер, который камера cam1/main подтвердила тремя голосами."""
    event = VehiclePlateEvent.objects.create(**{
        "event_id": uuid4(),
        "vehicle_number": "123ABC02",
        "camera": "cam1",
        "source": "main",
        "detected_at": timezone.now() - timedelta(seconds=3),
        "stationary_seconds": Decimal("3.400"),
        "confirmation_votes": 3,
        "detector_confidence": Decimal("0.9100"),
        "ocr_confidence": Decimal("0.9600"),
        "payload_json": {},
        **fields,
    })
    if received_at is not None:
        VehiclePlateEvent.objects.filter(pk=event.pk).update(received_at=received_at)
        event.refresh_from_db()
    return event


def silo_route(*, default=False, capacity=500_000, **silo_fields):
    """Тип зерна и его силос (имена Тип-N/Силос-N); ``default`` делает силос маршрутом типа."""
    grain_type = SiloType.objects.create(name=f"Тип-{SiloType.objects.count() + 1}")
    silo = Silo.objects.create(
        name=f"Силос-{Silo.objects.count() + 1}", total_capacity_kg=capacity, silo_type=grain_type, **silo_fields,
    )
    if default:
        grain_type.default_silo = silo
        grain_type.save(update_fields=["default_silo"])
    return grain_type, silo


def passage_trip(number="", *, status=st.ARRIVED, entry=None, ago=timedelta(minutes=5), **fields):
    """Рейс вывоза отрубей через автовесы."""
    return Wagon.objects.create(**{
        "number": number,
        "direction": Wagon.PASSAGE,
        "workflow": "simple",
        "cargo_name": "Отруби",
        "status": status,
        "arrived_at": timezone.now() - ago,
        "gross_weight_kg": entry,
        "number_source": "camera",
        **fields,
    })


def orientation_trip(number="854ANB13", *, status=st.COMPLETED, gross=3880, tare=8760, **fields):
    """Рейс для датасета ориентации: заезд пустым (front), выезд гружёным (rear)."""
    return passage_trip(
        number, status=status, entry=gross, ago=timedelta(hours=1), tare_weight_kg=tare, **fields,
    )


def weighing_record(wagon, kind, weight, *, orientation="", photo=True):
    """Взвешивание рейса с автовесов, по умолчанию со снимком."""
    record = WeighingRecord.objects.create(
        wagon=wagon, kind=kind, weight_kg=weight, source="scale", orientation=orientation
    )
    if photo:
        record.photo.save(f"{uuid4()}.jpg", ContentFile(JPEG), save=True)
    return record


def unassigned_weighing(weight=30_000, *, ago=timedelta(seconds=30), photo=True, **fields):
    """Вес без рейса («Неопознанные»), по умолчанию со снимком."""
    item = UnassignedWeighing.objects.create(**{
        "weight_kg": weight,
        "stable_weight_at": timezone.now() - ago,
        "scale_number": "truck",
        "scale_age_seconds": Decimal("0.2"),
        "camera": "cam1",
        "photo_request_id": uuid4(),
        **fields,
    })
    if photo:
        item.photo.save(f"{item.photo_request_id}.jpg", ContentFile(JPEG), save=True)
    return item


def orientation_sample(record_id, *, kind=VehicleOrientationSample.WEIGHING, label="front", source="trip", **fields):
    """Строка датасета ориентации напрямую, без исходного взвешивания."""
    return VehicleOrientationSample.objects.create(
        record_kind=kind,
        record_id=record_id,
        label=label,
        label_source=source,
        weight_kg=fields.pop("weight_kg", 4000),
        captured_at=fields.pop("captured_at", None) or timezone.now(),
        **fields,
    )
