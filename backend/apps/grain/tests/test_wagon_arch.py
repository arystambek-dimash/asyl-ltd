import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.grain import statuses as st
from apps.grain.models import GrainSupply, Silo, SiloType, Wagon, WagonArchStop

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 64


def test_wagon_arch_stop_is_unique_per_collector_event():
    now = timezone.now()
    stop = WagonArchStop.objects.create(
        stop_id=uuid.UUID("11111111-1111-1111-1111-111111111111"), camera="cam8",
        arrived_at=now, full_weight_kg=62340, photo_request_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert stop.status == WagonArchStop.OPEN and stop.wagon_id is None
    with pytest.raises(Exception):
        WagonArchStop.objects.create(
            stop_id=stop.stop_id, camera="cam8", arrived_at=now, full_weight_kg=1,
            photo_request_id=stop.stop_id,
        )


def test_runtime_last_stop_carries_the_blocked_detail_for_the_ui(media):
    """UI needs the free-text detail (m7): the reason code alone doesn't explain 'wrong_scale_action' etc."""
    from apps.grain import wagon_arch
    from django.core.cache import cache

    cache.clear()
    now = timezone.now()
    WagonArchStop.objects.create(
        stop_id=uuid.uuid4(), camera="cam8", arrived_at=now, full_weight_kg=62340,
        photo_request_id=uuid.uuid4(), status=WagonArchStop.ATTENTION,
        blocked_reason="wrong_scale_action", blocked_detail="весы ждут другое действие",
    )
    from weighbridge.outbox import Outbox
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        payload = wagon_arch._runtime_payload(Outbox(tmp), imported=0, discarded=0)
    assert payload["last_stop"]["blocked_detail"] == "весы ждут другое действие"


def _route(*, default=True, capacity=500_000, culture="пшеница", grain_class="3"):
    grain_type = SiloType.objects.create(name=f"Тип-{SiloType.objects.count() + 1}", grain_culture=culture, grain_class=grain_class)
    silo = Silo.objects.create(
        name=f"Силос-{Silo.objects.count() + 1}", total_capacity_kg=capacity, silo_type=grain_type,
        grain_culture=culture, grain_class=grain_class, unloading_line="линия 1",
    )
    if default:
        grain_type.default_silo = silo
        grain_type.save(update_fields=["default_silo"])
    return grain_type, silo


def _expected_wagon(number="28055531", *, with_silo=True, default=True, culture="пшеница", grain_class="3",
                    expected_weight_kg=40_000):
    grain_type, silo = _route(default=default, culture=culture, grain_class=grain_class)
    supply = GrainSupply.objects.create(
        supplier="ТОО Колос", grain_type=grain_type, assigned_silo=silo, expected_total_kg=expected_weight_kg,
        culture=culture, grain_class=grain_class, status="expected",
    )
    return Wagon.objects.create(
        supply=supply, number=number, direction=Wagon.INTAKE, workflow="simple", status=st.EXPECTED,
        expected_weight_kg=expected_weight_kg, assigned_silo=silo if with_silo else None,
    )


def test_default_route_silo_follows_the_grain_type_and_assigns_once():
    from apps.grain import services
    wagon = _expected_wagon(with_silo=True)
    silo = wagon.assigned_silo
    assert services.assign_default_silo(wagon) == silo          # already assigned: untouched

    wagon = _expected_wagon(number="28819852", with_silo=False)
    assigned = services.assign_default_silo(wagon)
    wagon.refresh_from_db()
    assert assigned is not None and wagon.assigned_silo_id == assigned.pk
    assert wagon.unloading_point == "линия 1"
    assert wagon.reservation.amount_kg == 40_000 and wagon.reservation.silo_id == assigned.pk

    no_route = _expected_wagon(
        number="28815116", with_silo=False, default=False, culture="ячмень", grain_class="2",
    )
    assert services.default_route_silo(no_route) is None
    assert services.assign_default_silo(no_route) is None
    no_route.refresh_from_db()
    assert no_route.assigned_silo_id is None

    bare = Wagon.objects.create(number="", direction=Wagon.INTAKE, workflow="simple", status=st.ARRIVED)
    assert services.default_route_silo(bare) is None


def _arrival(*, number="28055531", weight=62340, at=None, stop_id=None, photo=JPEG, error=""):
    stop_id = stop_id or uuid.uuid4()
    at = at or timezone.now() - timedelta(minutes=5)
    return {
        "version": 2, "kind": "wagon_stop", "id": str(stop_id), "camera": "cam8", "weight_kg": weight,
        "stable_weight_at": at.isoformat(), "scale_age_seconds": "0.1", "scale_updated_at": "t1", "still_seconds": 11.0,
        "photo": photo, "photo_error": "" if photo else "snapshot_unavailable",
        "number": number, "number_source": "model" if number else "", "recognition": None,
        "recognition_error": error, "ocr_attempts": 1,
    }


def _departure(stop, *, weight=24120, at=None):
    at = at or timezone.now() - timedelta(minutes=1)
    return {
        "version": 2, "kind": "wagon_departure", "id": str(uuid.uuid4()), "stop_id": stop["id"], "camera": "cam8",
        "weight_kg": weight, "stable_weight_at": at.isoformat() if weight is not None else None,
        "scale_updated_at": None, "departed_at": at.isoformat(), "motion_gap": False,
    }


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    settings.WAGON_ARCH_EXIT_GRACE_SECONDS = 600
    return tmp_path


def test_arrival_of_an_expected_wagon_records_the_entry_with_photo_and_silo(media):
    from apps.grain import wagon_arch
    from apps.grain.models import WeighingPhotoDelivery
    wagon = _expected_wagon(with_silo=False)
    event = _arrival()
    stop = wagon_arch.import_event(event)
    wagon.refresh_from_db()
    assert stop.wagon_id == wagon.pk and stop.status == WagonArchStop.OPEN and stop.blocked_reason == ""
    assert wagon.status == st.AT_SILO and wagon.gross_weight_kg == 62340
    assert wagon.assigned_silo_id is not None and wagon.number_source == "camera"
    assert wagon.number_camera_source == "cam8"
    assert wagon.arrived_at == stop.arrived_at             # время рейса = время стопа
    record = wagon.weighings.get(kind="gross")
    assert record.source == "scale" and record.scale_number == "wagon"
    assert record.photo_request_id == stop.stop_id and record.photo.read() == JPEG
    assert WeighingPhotoDelivery.objects.get(request_id=stop.stop_id).status == "saved"
    assert wagon_arch.import_event(event) == stop          # idempotent
    assert WagonArchStop.objects.count() == 1


def test_departure_is_applied_after_the_grace_period_and_completes_the_trip(media):
    from apps.grain import wagon_arch
    wagon = _expected_wagon(expected_weight_kg=38_220)
    arrival = _arrival()
    wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival, weight=24120, at=timezone.now() - timedelta(minutes=3)))
    stop = WagonArchStop.objects.get()
    assert stop.exit_weight_kg == 24120 and stop.exit_applied_at is None
    wagon.refresh_from_db()
    assert wagon.status == st.AT_SILO                      # grace: a re-positioning may still follow

    # Внутри льготного окна отъезд не применяется — вагон ещё могут переставить.
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=2))
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert stop.exit_applied_at is None and wagon.status == st.AT_SILO

    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=8))
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert stop.status == WagonArchStop.CLOSED and stop.exit_applied_at is not None
    assert (wagon.tare_weight_kg, wagon.net_weight_kg, wagon.status) == (24120, 38220, st.COMPLETED)


def test_a_discrepancy_still_closes_the_stop_and_is_not_an_import_problem(media):
    """Нетто вне допуска — обычный разбор оператора, а не ошибка импорта."""
    from apps.grain import wagon_arch
    wagon = _expected_wagon()                              # ожидание 40 000 против нетто 38 220
    arrival = _arrival()
    wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival, weight=24120, at=timezone.now() - timedelta(minutes=3)))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop = WagonArchStop.objects.get(); wagon.refresh_from_db()
    assert stop.status == WagonArchStop.CLOSED and stop.exit_applied_at is not None
    assert stop.blocked_reason == ""
    assert (wagon.tare_weight_kg, wagon.net_weight_kg) == (24120, 38220)
    assert wagon.status == st.WEIGHT_DISCREPANCY


def test_next_stop_applies_the_previous_departure_immediately(media):
    from apps.grain import wagon_arch
    first = _expected_wagon("28055531", expected_weight_kg=38_220)
    second = _expected_wagon("28819852")
    arrival = _arrival(number="28055531")
    wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival))
    wagon_arch.import_event(_arrival(number="28819852", at=timezone.now() - timedelta(seconds=30)))
    wagon_arch.apply_pending()
    first.refresh_from_db(); second.refresh_from_db()
    assert first.status == st.COMPLETED and second.status == st.AT_SILO
    assert list(WagonArchStop.objects.order_by("arrived_at").values_list("status", flat=True)) == ["closed", "open"]


def test_repositioned_wagon_continues_its_trip_instead_of_closing_it(media):
    from apps.grain import wagon_arch
    wagon = _expected_wagon("28055531", expected_weight_kg=38_220)
    first = _arrival(number="28055531", weight=62340, at=timezone.now() - timedelta(minutes=20))
    wagon_arch.import_event(first)
    wagon_arch.import_event(_departure(first, weight=50100, at=timezone.now() - timedelta(minutes=10)))
    second = _arrival(number="28055531", weight=49800, at=timezone.now() - timedelta(minutes=9))
    second_stop = wagon_arch.import_event(second)
    wagon_arch.apply_pending()
    wagon.refresh_from_db()
    assert wagon.status == st.AT_SILO and wagon.gross_weight_kg == 62340       # no second entry
    assert WagonArchStop.objects.get(stop_id=first["id"]).status == WagonArchStop.SUPERSEDED
    assert second_stop.wagon_id == wagon.pk and second_stop.entry_applied_at is not None
    wagon_arch.import_event(_departure(second, weight=24120))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    wagon.refresh_from_db()
    assert (wagon.tare_weight_kg, wagon.net_weight_kg, wagon.status) == (24120, 38220, st.COMPLETED)


def test_a_heavier_wagon_with_the_same_number_is_a_new_trip_not_a_continuation(media):
    from apps.grain import wagon_arch
    wagon = _expected_wagon("28055531", expected_weight_kg=38_220)
    first = _arrival(number="28055531", weight=62340, at=timezone.now() - timedelta(minutes=20))
    wagon_arch.import_event(first)
    wagon_arch.import_event(_departure(first, weight=24120, at=timezone.now() - timedelta(minutes=10)))
    wagon_arch.import_event(_arrival(number="28055531", weight=61000, at=timezone.now() - timedelta(minutes=9)))
    wagon_arch.apply_pending()
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    second_stop = WagonArchStop.objects.order_by("arrived_at").last()
    # Новый голый рейс без поставки: маршрута ★ нет, приход ждёт силос от оператора.
    assert second_stop.wagon_id != wagon.pk and second_stop.wagon.status == st.ARRIVED
    assert second_stop.blocked_reason == "silo_required"
    assert second_stop.wagon.supply_id is None and second_stop.wagon.number == "28055531"


def test_stop_without_a_default_route_waits_for_the_operator_then_applies(media):
    from apps.grain import wagon_arch
    wagon = _expected_wagon(with_silo=False, default=False, expected_weight_kg=38_220)
    arrival = _arrival()
    stop = wagon_arch.import_event(arrival)
    wagon.refresh_from_db()
    assert (stop.blocked_reason, wagon.status, wagon.gross_weight_kg) == ("silo_required", st.ARRIVED, None)
    wagon_arch.import_event(_departure(arrival))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop.refresh_from_db()
    assert stop.exit_applied_at is None                    # the exit waits behind the entry

    # Эмулируем оператора прямым назначением силоса (путь services.assign_silo).
    wagon.assigned_silo = wagon.supply.assigned_silo
    wagon.save(update_fields=["assigned_silo"])
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=12))
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert (stop.status, stop.blocked_reason, wagon.status) == (WagonArchStop.CLOSED, "", st.COMPLETED)


def test_unknown_number_opens_a_bare_trip_and_a_bad_exit_needs_attention(media):
    from apps.grain import wagon_arch
    arrival = _arrival(number="", error="number_unreadable")
    stop = wagon_arch.import_event(arrival)
    assert stop.wagon.number == "" and stop.wagon.supply_id is None and stop.wagon.status == st.ARRIVED
    assert stop.blocked_reason == "silo_required"          # no supply → no default route
    grain_type, silo = _route()
    stop.wagon.assigned_silo = silo
    stop.wagon.save(update_fields=["assigned_silo"])
    applied = wagon_arch.apply_pending()
    stop.refresh_from_db()
    assert stop.entry_applied_at is not None
    assert applied["entries_retried"] == 1                 # считаем только реально применённые
    # Повторный проход уже ничего не применяет.
    assert wagon_arch.apply_pending()["entries_retried"] == 0
    wagon_arch.import_event(_departure(arrival, weight=70000))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.ATTENTION, "exit_not_lower")
    assert stop.wagon.weighings.filter(kind="tare").count() == 0   # the rejected exit left no record

    other = wagon_arch.import_event(_arrival(number="", weight=60000, stop_id=uuid.uuid4()))
    # Приход должен примениться до отъезда: выезд ждёт за заблокированным приходом.
    _, other_silo = _route()
    other.wagon.assigned_silo = other_silo
    other.wagon.save(update_fields=["assigned_silo"])
    wagon_arch.apply_pending()
    other.refresh_from_db()
    assert other.entry_applied_at is not None
    wagon_arch.import_event(_departure({"id": str(other.stop_id)}, weight=None))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    other.refresh_from_db()
    assert (other.status, other.blocked_reason) == (WagonArchStop.ATTENTION, "no_exit_weight")


def test_poll_once_imports_acks_and_publishes_runtime(media, monkeypatch, tmp_path):
    from django.core.cache import cache
    from apps.grain import wagon_arch
    from weighbridge.outbox import Outbox
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "wagon"))
    box = Outbox(tmp_path / "wagon")
    _expected_wagon()
    arrival = _arrival(photo=None)
    box.put({k: v for k, v in arrival.items() if k != "photo"})
    box.finish(arrival["id"], "photo", photo=JPEG, updates={"photo_error": ""})
    box.finish(arrival["id"], "ocr", updates={"number": "28055531", "number_source": "model", "recognition": None, "recognition_error": "", "ocr_attempts": 1})
    # Нечитаемые тела не держат очередь: обе строки подтверждаются после предупреждения.
    mystery_v2 = str(uuid.uuid4())
    box.put({"version": 2, "kind": "mystery", "id": mystery_v2})
    box.finish(mystery_v2, "photo", updates={})
    box.finish(mystery_v2, "ocr", updates={})
    mystery_v9 = str(uuid.uuid4())
    box.put({"version": 9, "kind": "mystery", "id": mystery_v9})
    box.finish(mystery_v9, "photo", updates={})
    box.finish(mystery_v9, "ocr", updates={})
    box.state("heartbeat", {"updated_at": 0, "status": "running", "standing": None, "motion": "still", "pending_writes": 0})
    cache.clear()
    assert wagon_arch.enabled() is True
    result = wagon_arch.poll_once()
    assert result["imported"] == 1 and result["discarded"] == 2
    assert box.counts()["pending"] == 0                    # очередь никогда не встаёт
    runtime = wagon_arch.runtime()
    assert runtime["enabled"] is True and runtime["camera"] == "cam8"
    assert runtime["last_stop"]["number"] == "28055531" and runtime["collector"]["status"] == "running"
    assert runtime["discarded"] == 2
    settings_flag = wagon_arch.enabled
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "missing"))
    assert settings_flag() is False


def test_malformed_bodies_are_discarded_and_never_block_the_queue(media, monkeypatch, tmp_path):
    """KeyError/TypeError тоже мусор: очередь не должна вставать (ruling 5)."""
    from django.core.cache import cache
    from apps.grain import wagon_arch
    from weighbridge.outbox import Outbox
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "wagon"))
    box = Outbox(tmp_path / "wagon")
    _expected_wagon()

    def _ready(body):
        box.put(body)
        box.finish(body["id"], "photo", updates={})
        box.finish(body["id"], "ocr", updates={})

    broken = _arrival(photo=None)
    broken.pop("stable_weight_at")                          # обязательный ключ отсутствует
    _ready({k: v for k, v in broken.items() if k != "photo"})
    no_stop_id = {"version": 2, "kind": "wagon_departure", "id": str(uuid.uuid4()), "camera": "cam8"}
    _ready(no_stop_id)
    bad_id = {"version": 2, "kind": "wagon_stop", "id": "not-a-uuid", "camera": "cam8"}
    _ready(bad_id)
    # Некорректный id отвергается до любой работы с телом.
    with pytest.raises(ValueError):
        wagon_arch.import_event(dict(bad_id))
    with pytest.raises(ValueError):
        wagon_arch.import_event({"version": 2, "kind": "wagon_stop", "camera": "cam8"})
    valid = _arrival(photo=None)
    _ready({k: v for k, v in valid.items() if k != "photo"})
    cache.clear()

    result = wagon_arch.poll_once()
    assert result["discarded"] == 3                          # три нечитаемых тела
    assert result["imported"] == 1                           # исправное событие всё равно прошло
    assert box.counts()["pending"] == 0
    assert WagonArchStop.objects.count() == 1
    assert str(WagonArchStop.objects.get().stop_id) == valid["id"]


def test_a_future_timestamp_is_rejected_like_the_truck_importer(media):
    from apps.grain import wagon_arch
    _expected_wagon()
    ahead = _arrival(at=timezone.now() + timedelta(hours=1))
    with pytest.raises(ValueError):
        wagon_arch.import_event(ahead)
    assert WagonArchStop.objects.count() == 0

    # Небольшой перекос часов сборщика допустим.
    skewed = _arrival(at=timezone.now() + timedelta(minutes=1))
    assert wagon_arch.import_event(skewed) is not None

    arrival = _arrival(stop_id=uuid.uuid4(), at=timezone.now() - timedelta(minutes=5))
    wagon_arch.import_event(arrival)
    with pytest.raises(ValueError):
        wagon_arch.import_event(_departure(arrival, at=timezone.now() + timedelta(hours=1)))
    assert WagonArchStop.objects.get(stop_id=arrival["id"]).departure_id is None


def test_a_concurrent_duplicate_import_returns_the_existing_stop(media, monkeypatch):
    """Две пересекающиеся итерации: IntegrityError → вернуть уже созданный стоп."""
    from apps.grain import wagon_arch
    from apps.grain.models import WagonArchStop as Model
    _expected_wagon()
    event = _arrival()

    # «Другая» итерация уже зафиксировала стоп; наш insert падает IntegrityError.
    # Патчим сам поиск, чтобы пройти мимо ранней проверки на существующий стоп.
    committed = Model.objects.create(
        stop_id=uuid.UUID(event["id"]), camera=event["camera"],
        arrived_at=timezone.now() - timedelta(minutes=5), full_weight_kg=event["weight_kg"],
        photo_request_id=uuid.UUID(event["id"]),
    )
    original_filter = Model.objects.filter
    seen = {"n": 0}

    def blind_filter(*args, **kwargs):
        queryset = original_filter(*args, **kwargs)
        if "stop_id" in kwargs and seen["n"] == 0:
            seen["n"] = 1
            return queryset.none()                           # ранняя проверка «не видит» строку
        return queryset

    monkeypatch.setattr(Model.objects, "filter", blind_filter)
    stop = wagon_arch.import_event(event)
    monkeypatch.undo()
    assert stop is not None and stop.pk == committed.pk      # вернулся уже созданный стоп
    assert Model.objects.count() == 1                        # без дублей


def test_runtime_and_stops_api_require_grain_view(media, auth_client, user_with_perms, monkeypatch):
    from apps.grain import wagon_arch
    from apps.grain.models import WeighingPhotoDelivery
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(media / "wagon"))
    (media / "wagon").mkdir()
    viewer = user_with_perms("arch-viewer", codes=["grain.view"])
    denied = user_with_perms("arch-denied", codes=[])
    _expected_wagon()
    stop = wagon_arch.import_event(_arrival())
    assert auth_client(denied).get("/api/grain/wagon-arch/stops/").status_code == 403
    page = auth_client(viewer).get("/api/grain/wagon-arch/stops/")
    assert page.status_code == 200
    [row] = page.data["results"]
    assert (row["number"], row["full_weight_kg"], row["status"], row["wagon_status"]) == ("28055531", 62340, "open", st.AT_SILO)
    assert row["photo_url"].startswith("/api/grain/photos/evidence/")
    assert auth_client(viewer).get("/api/grain/wagon-arch/stops/", {"before": row["id"]}).data["results"] == []
    assert auth_client(viewer).get("/api/grain/wagon-arch/stops/", {"before": "x"}).status_code == 400
    runtime = auth_client(viewer).get("/api/grain/wagon-arch/runtime/")
    assert runtime.status_code == 200 and runtime.data["enabled"] is True and runtime.data["camera"] == "cam8"


def test_camera_plate_poll_is_off_while_the_arch_automation_runs(settings, monkeypatch):
    """m10: одна настоящая итерация `--once`, а не только предикат."""
    from io import StringIO
    from types import SimpleNamespace
    from unittest.mock import patch
    from django.core.management import call_command
    from apps.cameras import ai, continuous
    from apps.cameras.management.commands.monitor_cameras import Command
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    assert Command.wagon_plate_poll_enabled() is False
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    health_state = SimpleNamespace(
        status="healthy", observed_status="healthy", online_count=8,
        expected_count=8, failure_streak=0, recovery_streak=1,
    )
    with (
        patch(
            "apps.cameras.management.commands.monitor_cameras.health.monitor_once",
            return_value=health_state,
        ),
        patch.object(continuous, "reconcile", return_value={"cameras": ["cam2"]}) as reconcile,
        patch.object(
            continuous, "reconcile_wagon_number", return_value={"camera": "cam8"},
        ) as reconcile_wagon,
        patch.object(continuous, "poll_wagon_plate") as poll,
    ):
        call_command("monitor_cameras", "--once", stdout=StringIO())
    poll.assert_not_called()                                  # арка — датчик прибытия
    reconcile.assert_called_once()                            # остальная сверка работает
    reconcile_wagon.assert_called_once()

    # Флаг снят — опрос таблички возвращается.
    settings.WAGON_ARCH_AUTOMATION_ENABLED = False
    assert Command.wagon_plate_poll_enabled() is True
    with (
        patch(
            "apps.cameras.management.commands.monitor_cameras.health.monitor_once",
            return_value=health_state,
        ),
        patch.object(continuous, "reconcile", return_value={"cameras": ["cam2"]}),
        patch.object(continuous, "reconcile_wagon_number", return_value={"camera": "cam8"}),
        patch.object(continuous, "poll_wagon_plate", return_value={"seen": False}) as poll,
    ):
        call_command("monitor_cameras", "--once", stdout=StringIO())
    poll.assert_called_once_with()


# ── Итоговая волна правок (C1—C3, I4—I7, мелочи) ────────────────────────────


def test_a_deleted_wagon_parks_the_stop_instead_of_killing_the_monitor(media):
    """C1a: рейс удалён из CRM — стоп уходит в ATTENTION, монитор живёт."""
    from apps.grain import services, wagon_arch
    wagon = _expected_wagon(expected_weight_kg=38_220)
    arrival = _arrival()
    stop = wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival, weight=24120))
    wagon.refresh_from_db()
    services.delete_wagon(wagon, None, reason="ошибочная запись")

    result = wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop.refresh_from_db()
    assert stop.wagon_id is None
    assert (stop.status, stop.blocked_reason, stop.blocked_detail) == (
        WagonArchStop.ATTENTION, "wagon_deleted", "Рейс удалён",
    )
    assert result["departures_applied"] == 0
    # Терминальный стоп больше не перебирается.
    assert wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=12)) == {
        "entries_retried": 0, "departures_applied": 0,
    }


def test_an_unexpected_error_parks_the_stop_and_the_tick_survives(media, monkeypatch):
    """C1b: неожиданная ошибка в логике рейса не роняет apply_pending."""
    from apps.grain import services, wagon_arch
    wagon = _expected_wagon(expected_weight_kg=38_220)
    arrival = _arrival()
    stop = wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival, weight=24120))

    def boom(*args, **kwargs):
        raise RuntimeError("весы ответили ерундой")

    monkeypatch.setattr(services, "record_simple_exit_weight", boom)
    result = wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    monkeypatch.undo()
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.ATTENTION, "import_error")
    assert "весы ответили ерундой" in stop.blocked_detail
    assert result["departures_applied"] == 0
    assert wagon.status == st.AT_SILO                       # ничего не записано


def test_poll_once_does_not_let_trip_errors_escape(media, monkeypatch, tmp_path):
    """C1b: poll_once переживает падение в логике рейса (монитор жив)."""
    from django.core.cache import cache
    from apps.grain import services, wagon_arch
    from weighbridge.outbox import Outbox
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "wagon"))
    Outbox(tmp_path / "wagon")
    wagon = _expected_wagon(with_silo=False, default=False)
    arrival = _arrival()
    stop = wagon_arch.import_event(arrival)
    assert stop.blocked_reason == "silo_required"             # приход ещё не применён
    wagon.refresh_from_db()
    wagon.assigned_silo = wagon.supply.assigned_silo
    wagon.save(update_fields=["assigned_silo"])
    cache.clear()

    def boom(*args, **kwargs):
        raise RuntimeError("сервис упал")

    monkeypatch.setattr(services, "record_simple_entry_weight", boom)
    result = wagon_arch.poll_once()                          # не должно бросить
    monkeypatch.undo()
    stop.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.ATTENTION, "import_error")
    assert result["imported"] == 0


def test_continuation_applies_the_root_entry_not_the_mid_unloading_weight(media):
    """C2: продолжение берёт вес и кадр корневого стопа, а не свой."""
    from apps.grain import wagon_arch
    wagon = _expected_wagon("28055531", with_silo=False, default=False, expected_weight_kg=38_220)
    first = _arrival(number="28055531", weight=62340, at=timezone.now() - timedelta(minutes=20))
    root = wagon_arch.import_event(first)
    assert root.blocked_reason == "silo_required"            # приход ещё не применён
    wagon_arch.import_event(_departure(first, weight=50100, at=timezone.now() - timedelta(minutes=10)))
    second = _arrival(number="28055531", weight=49800, at=timezone.now() - timedelta(minutes=9))
    second_stop = wagon_arch.import_event(second)
    second_stop.refresh_from_db()
    assert second_stop.continues_id == root.pk
    assert second_stop.full_weight_kg == 49800               # свой вес журнала не меняется

    wagon.assigned_silo = wagon.supply.assigned_silo
    wagon.save(update_fields=["assigned_silo"])
    wagon_arch.apply_pending()
    wagon.refresh_from_db()
    assert wagon.gross_weight_kg == 62_340                   # вес корня, не середина выгрузки
    record = wagon.weighings.get(kind="gross")
    assert record.photo_request_id == root.stop_id           # кадр корневого стопа

    wagon_arch.import_event(_departure(second, weight=24120))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    wagon.refresh_from_db()
    assert (wagon.tare_weight_kg, wagon.net_weight_kg, wagon.status) == (24120, 38220, st.COMPLETED)


def test_a_retried_open_trip_keeps_its_wagon_when_the_entry_blocks(media):
    """C3: повтор не откатывает open_trip вместе с заблокированным приходом."""
    from apps.grain import wagon_arch
    blocker = _expected_wagon("28055531", expected_weight_kg=38_220)
    blocker.status = st.AT_SILO
    blocker.save(update_fields=["status"])
    stop = wagon_arch.import_event(_arrival(number="28055531"))
    assert (stop.blocked_reason, stop.wagon_id) == ("wagon_on_site", None)

    # Блокирующий рейс ушёл: следующий тик открывает голый рейс без маршрута ★.
    blocker.status = st.COMPLETED
    blocker.save(update_fields=["status"])
    wagon_arch.apply_pending()
    stop.refresh_from_db()
    assert stop.wagon_id is not None                          # рейс НЕ откатился
    assert stop.blocked_reason == "silo_required"
    created = stop.wagon_id

    wagon_arch.apply_pending()
    stop.refresh_from_db()
    assert stop.wagon_id == created                           # и не пересоздаётся каждый тик
    assert Wagon.objects.filter(number="28055531", supply__isnull=True).count() == 1


def test_a_motion_gap_departure_is_not_an_ordinary_exit(media):
    """I4: отъезд не был виден — вес выезда подтверждает оператор."""
    from apps.grain import wagon_arch
    wagon = _expected_wagon(expected_weight_kg=38_220)
    arrival = _arrival()
    wagon_arch.import_event(arrival)
    departure = _departure(arrival, weight=24120)
    departure["motion_gap"] = True
    wagon_arch.import_event(departure)
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop = WagonArchStop.objects.get(); wagon.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.ATTENTION, "exit_unseen")
    assert stop.blocked_detail == "Отъезд не был виден — подтвердите вес выезда"
    assert stop.exit_weight_kg == 24120 and stop.exit_applied_at is None
    assert wagon.tare_weight_kg is None and wagon.status == st.AT_SILO


def test_a_manual_exit_closes_the_stop_as_recorded_by_hand(media):
    """I5a: оператор записал выезд сам — стоп закрывается без ошибки."""
    from apps.grain import services, wagon_arch
    wagon = _expected_wagon(expected_weight_kg=38_220)
    arrival = _arrival()
    wagon_arch.import_event(arrival)
    wagon.refresh_from_db()
    services.record_simple_exit_weight(
        wagon, 24120, None, source="manual", manual_reason="весовщик записал сам",
    )
    wagon_arch.import_event(_departure(arrival, weight=24500))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop = WagonArchStop.objects.get()
    assert (stop.status, stop.blocked_reason, stop.blocked_detail) == (
        WagonArchStop.CLOSED, "", "записано вручную",
    )


def test_a_manual_entry_marks_the_stop_entry_as_applied(media):
    """I5a: входной вес уже записан руками — приход считается применённым."""
    from apps.grain import services, wagon_arch
    wagon = _expected_wagon(with_silo=False, default=False, expected_weight_kg=38_220)
    stop = wagon_arch.import_event(_arrival())
    assert stop.blocked_reason == "silo_required"
    wagon.refresh_from_db()
    wagon.assigned_silo = wagon.supply.assigned_silo
    wagon.save(update_fields=["assigned_silo"])
    services.record_simple_entry_weight(
        wagon, 62000, None, source="manual", manual_reason="весовщик записал сам",
    )

    wagon_arch.apply_pending()
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert stop.entry_applied_at is not None
    assert (stop.blocked_reason, stop.blocked_detail) == ("", "записано вручную")
    assert wagon.gross_weight_kg == 62000                    # ручной вес не перезаписан
    assert wagon.weighings.filter(kind="gross").count() == 1


def test_a_non_retryable_code_parks_the_stop_immediately(media):
    """I5b: not_simple_flow — терминальная причина, без бесконечных повторов."""
    from apps.grain import wagon_arch
    wagon = _expected_wagon(expected_weight_kg=38_220)
    wagon.workflow = "full"
    wagon.save(update_fields=["workflow"])
    stop = wagon_arch.import_event(_arrival())
    stop.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.ATTENTION, "not_simple_flow")
    assert wagon_arch.apply_pending()["entries_retried"] == 0


def test_dismiss_closes_a_stop_for_the_operator(media, auth_client, user_with_perms):
    """I5c: POST .../dismiss/ — ручное закрытие любого незакрытого стопа."""
    from apps.grain import wagon_arch
    editor = user_with_perms("arch-editor", codes=["grain.weigh"])
    viewer = user_with_perms("arch-onlyview", codes=["grain.view"])
    _expected_wagon()
    stop = wagon_arch.import_event(_arrival())
    url = f"/api/grain/wagon-arch/stops/{stop.pk}/dismiss/"
    assert auth_client(viewer).post(url).status_code == 403
    response = auth_client(editor).post(url)
    assert response.status_code == 200
    assert response.data["status"] == "closed"
    assert response.data["blocked_detail"] == "закрыто оператором"
    stop.refresh_from_db()
    assert (stop.status, stop.blocked_reason, stop.exit_applied_at) == (
        WagonArchStop.CLOSED, "", None,
    )
    # Ручное закрытие — действие оператора, оно обязано быть в журнале рейса.
    from apps.eventlog.models import EventLog
    entry = EventLog.objects.get(
        event_type="grain_arch", message__icontains="закрыта оператором",
    )
    assert entry.user_id == editor.pk
    assert entry.payload["wagon_id"] == stop.wagon_id
    assert entry.payload["auto"] is False
    assert str(stop.stop_id) in entry.message
    assert auth_client(editor).post("/api/grain/wagon-arch/stops/999999/dismiss/").status_code == 404


def test_the_exit_weighing_and_the_closed_save_are_one_transaction(media, monkeypatch):
    """I7: если сохранение стопа упадёт, выходной вес тоже откатывается."""
    from apps.grain import wagon_arch
    from apps.grain.models import WagonArchStop as Model
    wagon = _expected_wagon(expected_weight_kg=38_220)
    arrival = _arrival()
    wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival, weight=24120))
    original_save = Model.save

    def failing_save(self, *args, **kwargs):
        fields = kwargs.get("update_fields") or ()
        if "exit_applied_at" in fields:
            raise RuntimeError("сеть до БД моргнула")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Model, "save", failing_save)
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    monkeypatch.undo()
    wagon.refresh_from_db()
    assert wagon.tare_weight_kg is None                      # взвешивание откатилось
    assert wagon.weighings.filter(kind="tare").count() == 0


def test_blocked_and_clear_block_save_only_on_change(media):
    """m8: одинаковая причина не пишет строку заново каждые 2—5 секунд."""
    from apps.grain import wagon_arch
    _expected_wagon(with_silo=False, default=False)
    stop = wagon_arch.import_event(_arrival())
    assert stop.blocked_reason == "silo_required"
    stop.refresh_from_db()
    before = stop.updated_at
    wagon_arch.apply_pending()
    stop.refresh_from_db()
    assert stop.updated_at == before                         # ничего не изменилось — не сохраняем


def test_an_old_pending_departure_does_not_capture_a_new_stop(media):
    """m13: стоп, уехавший давно, не может «продолжиться» новым вагоном."""
    from apps.grain import wagon_arch
    grace = 600
    long_ago = timezone.now() - timedelta(seconds=grace * 2 + 3600)
    _expected_wagon("28055531", expected_weight_kg=38_220)
    first = _arrival(number="28055531", weight=62340, at=long_ago)
    stale = wagon_arch.import_event(first)
    wagon_arch.import_event(_departure(first, weight=50100, at=long_ago + timedelta(minutes=1)))
    # Тот же номер спустя сутки — это новый рейс, а не пересдача.
    second = wagon_arch.import_event(_arrival(number="28055531", weight=49800))
    second.refresh_from_db(); stale.refresh_from_db()
    assert second.continues_id is None
    assert stale.status != WagonArchStop.SUPERSEDED


def test_an_unnumbered_stop_within_the_grace_window_continues_by_weight(media):
    """m16: номер не распознан, но вес сходится — тот же рейс продолжается."""
    from apps.grain import wagon_arch
    from apps.eventlog.models import EventLog
    wagon = _expected_wagon("28055531", expected_weight_kg=38_220)
    first = _arrival(number="28055531", weight=62340, at=timezone.now() - timedelta(minutes=20))
    root = wagon_arch.import_event(first)
    wagon_arch.import_event(_departure(first, weight=50100, at=timezone.now() - timedelta(minutes=10)))
    second = wagon_arch.import_event(
        _arrival(number="", weight=49800, at=timezone.now() - timedelta(minutes=9))
    )
    second.refresh_from_db(); root.refresh_from_db()
    assert second.continues_id == root.pk
    assert root.status == WagonArchStop.SUPERSEDED
    assert second.wagon_id == wagon.pk
    assert EventLog.objects.filter(
        event_type="grain_arch",
        message__icontains="номер не распознан, рейс продолжен по весу",
        payload__wagon_id=wagon.pk,
    ).exists()
    wagon.refresh_from_db()
    assert wagon.gross_weight_kg == 62_340                   # второго прихода нет


def test_a_heavier_unnumbered_stop_is_a_new_trip(media):
    """m16: вес вырос — это следующий вагон, продолжения нет."""
    from apps.grain import wagon_arch
    _expected_wagon("28055531", expected_weight_kg=38_220)
    first = _arrival(number="28055531", weight=62340, at=timezone.now() - timedelta(minutes=20))
    root = wagon_arch.import_event(first)
    wagon_arch.import_event(_departure(first, weight=24120, at=timezone.now() - timedelta(minutes=10)))
    second = wagon_arch.import_event(
        _arrival(number="", weight=61000, at=timezone.now() - timedelta(minutes=9))
    )
    second.refresh_from_db(); root.refresh_from_db()
    assert second.continues_id is None
    assert root.status == WagonArchStop.CLOSED


def test_the_serializer_survives_a_stop_without_a_wagon(media, auth_client, user_with_perms):
    """m14: строка wagon_on_site (wagon=None) сериализуется без падения."""
    from apps.grain import wagon_arch
    viewer = user_with_perms("arch-viewer2", codes=["grain.view"])
    blocker = _expected_wagon("28055531")
    blocker.status = st.AT_SILO
    blocker.save(update_fields=["status"])
    stop = wagon_arch.import_event(_arrival(number="28055531"))
    assert stop.wagon_id is None
    page = auth_client(viewer).get("/api/grain/wagon-arch/stops/")
    [row] = page.data["results"]
    assert row["wagon_id"] is None and row["wagon_status"] == ""
    assert row["continues"] is None
    assert row["blocked_reason"] == "wagon_on_site"


def test_a_wagon_deleted_before_the_entry_applied_parks_the_stop(media):
    """C1a: удалённый до применения прихода рейс не пересоздаётся заново."""
    from apps.grain import services, wagon_arch
    wagon = _expected_wagon(with_silo=False, default=False)
    stop = wagon_arch.import_event(_arrival())
    assert (stop.blocked_reason, stop.wagon_id) == ("silo_required", wagon.pk)
    wagon.refresh_from_db()
    services.delete_wagon(wagon, None, reason="ошибочная запись")

    wagon_arch.apply_pending()
    stop.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.ATTENTION, "wagon_deleted")
    assert stop.wagon_id is None
    assert Wagon.objects.count() == 0                        # новый рейс не создан


def test_a_database_error_reaches_the_monitor_instead_of_parking_the_stop(media, monkeypatch):
    """Раунд 2: временный сбой БД — дело монитора, а не терминальная причина."""
    from django.db import OperationalError
    from apps.grain import services, wagon_arch
    wagon = _expected_wagon(expected_weight_kg=38_220)
    arrival = _arrival()
    stop = wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival, weight=24120))

    def dropped(*args, **kwargs):
        raise OperationalError("connection reset")

    monkeypatch.setattr(services, "record_simple_exit_weight", dropped)
    with pytest.raises(OperationalError):
        wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    monkeypatch.undo()
    stop.refresh_from_db()
    # Стоп остаётся живым: следующий тик повторит выезд, когда БД вернётся.
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.OPEN, "")
    assert stop.exit_applied_at is None

    # БД вернулась — обычный тик доводит рейс до конца.
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=12))
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert stop.status == WagonArchStop.CLOSED
    assert (wagon.tare_weight_kg, wagon.status) == (24120, st.COMPLETED)


def test_a_database_error_on_the_entry_also_reaches_the_monitor(media, monkeypatch):
    """Раунд 2: тот же контракт на дорожке приходов."""
    from django.db import OperationalError
    from apps.grain import wagon_arch
    wagon = _expected_wagon(with_silo=False, default=False)
    stop = wagon_arch.import_event(_arrival())
    assert stop.blocked_reason == "silo_required"
    wagon.refresh_from_db()
    wagon.assigned_silo = wagon.supply.assigned_silo
    wagon.save(update_fields=["assigned_silo"])

    def dropped(*args, **kwargs):
        raise OperationalError("connection reset")

    monkeypatch.setattr(wagon_arch, "apply_entry", dropped)
    with pytest.raises(OperationalError):
        wagon_arch.apply_pending()
    monkeypatch.undo()
    stop.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.OPEN, "silo_required")
    assert stop.entry_applied_at is None


def test_dismissing_a_stop_without_a_wagon_writes_no_trip_line(media, auth_client, user_with_perms):
    """Раунд 2: у стопа без рейса журнал писать некуда — и это не ошибка."""
    from apps.eventlog.models import EventLog
    from apps.grain import wagon_arch
    editor = user_with_perms("arch-editor2", codes=["grain.weigh"])
    blocker = _expected_wagon("28055531")
    blocker.status = st.AT_SILO
    blocker.save(update_fields=["status"])
    stop = wagon_arch.import_event(_arrival(number="28055531"))
    assert stop.wagon_id is None
    response = auth_client(editor).post(f"/api/grain/wagon-arch/stops/{stop.pk}/dismiss/")
    assert response.status_code == 200 and response.data["status"] == "closed"
    assert not EventLog.objects.filter(
        event_type="grain_arch", message__icontains="закрыта оператором",
    ).exists()
