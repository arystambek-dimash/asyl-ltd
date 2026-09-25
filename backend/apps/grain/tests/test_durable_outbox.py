import sqlite3
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.grain import outbox_importer
from apps.grain.models import AutomaticPassageCapture, UnassignedWeighing, WeighingPhotoDelivery
from apps.grain.tests.factories import (
    JPEG,
    collector_event,
    fake_time,
    outbox_event,
    process_without_gpt,
    scale_observation,
)
from weighbridge.outbox import Outbox, Lane
from weighbridge.collector import Collector


def test_stable_truck_after_moving_is_captured_once_then_next_after_clear():
    lane=Lane(stable_seconds=2)
    for t in range(3): assert not lane.observe(scale_observation(0,t),t)
    assert not lane.observe(scale_observation(2000,3,state="unstable"),3)
    assert not lane.observe(scale_observation(4000,4),4)
    assert not lane.observe(scale_observation(4000,5),5)
    assert lane.observe(scale_observation(4000,6),6)
    lane.captured()
    for t in range(7,20): assert not lane.observe(scale_observation(4000,t),t)
    for t in range(20,23): assert not lane.observe(scale_observation(0,t),t)
    assert not lane.observe(scale_observation(4200,23),23)
    assert lane.observe(scale_observation(4200,25),25)


def test_restart_gap_or_duplicate_reading_cannot_invent_a_new_occupancy():
    lane=Lane(stable_seconds=2)
    for t in range(10): assert not lane.observe(scale_observation(4000,t),t)
    for t in range(10,13): lane.observe(scale_observation(0,t),t)
    for t in range(13,17): assert not lane.observe(scale_observation(4000,13),t)
    lane.gap()
    for t in range(17,25): assert not lane.observe(scale_observation(4000,t),t)


def test_queue_restart_order_immutable_evidence_and_ack(tmp_path):
    box=Outbox(tmp_path); a,b=outbox_event(),outbox_event()
    box.put(a);box.put(b)
    box.finish(b["id"],"photo",photo=b"second");box.finish(b["id"],"ocr")
    assert box.next() is None  # cannot overtake an unfinished entry
    box.finish(a["id"],"photo",photo=b"first")
    restarted=Outbox(tmp_path);restarted.recover()
    assert restarted.next()["photo"] == b"first"
    restarted.finish(a["id"],"photo",photo=b"later truck")
    assert restarted.next()["photo"] == b"first"
    restarted.ack(a["id"]);assert restarted.next()["id"] == b["id"]
    assert restarted.counts() == {"total":2,"pending":1}


def test_killed_writer_rolls_back_partial_write_without_losing_committed_event(tmp_path):
    import subprocess, sys
    box=Outbox(tmp_path);value=outbox_event();box.put(value)
    script="""import sqlite3,sys,os
db=sqlite3.connect(sys.argv[1]);db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE events SET body=?', ('corrupt unfinished value',))
os._exit(9)
"""
    result=subprocess.run([sys.executable,"-c",script,str(box.path)],check=False)
    assert result.returncode == 9
    reopened=Outbox(tmp_path);reopened.recover()
    assert reopened.next()["id"] == value["id"]
    assert reopened.next()["weight_kg"] == 4200


def test_late_camera_response_cannot_attach_to_next_truck(tmp_path):
    collector,box,value=collector_event(tmp_path)
    def camera(*args, **kwargs):
        collector.current=str(uuid4())
        return {"vehicle_number":"123ABC13", "orientation":{"label":"rear","confidence":1}}
    with patch("apps.cameras.ai.recognize_vehicle_from_camera",side_effect=camera):
        collector.recognize(value)
    collector.close()
    box.finish(value["id"],"photo")
    assert box.next()["recognition"] is None
    assert box.next()["orientation"] == ""
    assert box.next()["recognition_error"] == "recognition_after_departure"
    collector.close()


def test_camera_failure_cannot_lose_already_persisted_weight(tmp_path):
    collector,box,value=collector_event(tmp_path)
    with patch("apps.grain.scale.open_local_request",side_effect=TimeoutError):
        collector.snapshot(value)
    collector.close()
    box.finish(value["id"],"ocr")
    assert box.next()["weight_kg"] == 4200
    assert box.next()["photo"] is None
    collector.close()


@pytest.mark.django_db(transaction=True)
def test_database_commit_before_ack_is_idempotent_after_crash(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT=tmp_path/"media"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED=True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=True
    settings.WEIGHING_AI_ENABLED=False
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR",str(tmp_path))
    box=Outbox(tmp_path);value=outbox_event();box.put(value)
    box.finish(value["id"],"photo",photo=b"\xff\xd8image");box.finish(value["id"],"ocr")
    with patch.object(Outbox,"ack",side_effect=OSError("process died after commit")):
        with pytest.raises(OSError): outbox_importer.poll_once()
    assert AutomaticPassageCapture.objects.count() == 1
    assert UnassignedWeighing.objects.count() == 1
    with patch("apps.grain.scale.read_truck_scale",side_effect=AssertionError("must replay")):
        outbox_importer.poll_once()
    assert AutomaticPassageCapture.objects.count() == 1 and UnassignedWeighing.objects.count() == 1
    assert box.counts()["pending"] == 0
    assert WeighingPhotoDelivery.objects.get().photo.read() == b"\xff\xd8image"


@pytest.mark.django_db
def test_database_failure_keeps_weight_and_photo_for_retry(tmp_path):
    box=Outbox(tmp_path);value=outbox_event();box.put(value);box.finish(value["id"],"photo",photo=b"photo");box.finish(value["id"],"ocr")
    with patch.object(AutomaticPassageCapture.objects,"get_or_create",side_effect=OSError("database offline")):
        with pytest.raises(OSError): outbox_importer.import_event(box.next())
    assert box.next()["photo"] == b"photo" and box.counts()["pending"] == 1


@pytest.mark.django_db(transaction=True)
def test_failed_apply_without_ai_parks_the_collector_weight_for_the_operator(tmp_path, settings):
    """ИИ выключен, оформление отказало (4xx): вес сборщика уходит в «Неопознанные», а не теряется."""
    from rest_framework.exceptions import ValidationError
    from apps.grain import services
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.WEIGHING_AI_ENABLED = False
    value = outbox_event()
    value["photo"] = b"\xff\xd8failed apply"
    refusal = ValidationError({"detail": "Рейс уже закрыт.", "code": "passage_rejected"})
    with patch.object(services, "apply_unidentified_passage_scale_sample", side_effect=refusal):
        capture = outbox_importer.import_event(value)
    assert capture.status == AutomaticPassageCapture.FAILED
    capture.refresh_from_db()
    assert capture.requires_acknowledgement is False  # оператор разбирает вес, а не подтверждает сбой
    parked = UnassignedWeighing.objects.get(capture=capture)
    assert parked.weight_kg == 4200 and parked.reason == capture.error_code
    assert parked.photo_request_id == capture.idempotency_key
    assert parked.photo.read() == value["photo"]


@pytest.mark.django_db
def test_acknowledging_a_failure_keeps_the_collector_runtime(tmp_path, settings, monkeypatch, user_with_perms):
    """Подтверждение старого сбоя не затирает состояние сборщика в кэше до следующего опроса."""
    from django.core.cache import cache
    from apps.grain import passage_scale_automation as automation
    from apps.grain.models import PassageScaleAutomationState
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR", str(tmp_path))
    (tmp_path / "enabled").write_text("1")
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(), camera="cam1", status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE, error_code="vehicle_recognition_unavailable", completed_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.update_or_create(scale_number="truck", defaults={"current_capture": capture})
    collector = {"total": 3, "pending": 0, "status": "running"}
    cache.set(automation.RUNTIME_CACHE_KEY, {
        "enabled": True, "state": "idle", "heartbeat_stale": False, "active": None,
        "last_checked_at": timezone.now().isoformat(), "stable_weight_seconds": 3, "collector": collector,
    })
    runtime = automation.acknowledge_failure(capture.idempotency_key, user=user_with_perms("ack", codes=["grain.weigh"]))
    assert runtime["collector"] == collector
    assert cache.get(automation.RUNTIME_CACHE_KEY)["collector"] == collector


@pytest.mark.django_db(transaction=True)
def test_enabled_monitor_restart_replays_without_touching_hardware(tmp_path, settings, monkeypatch):
    from django.core.management import call_command
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR", str(tmp_path))
    (tmp_path / "enabled").write_text("1")
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(tmp_path / "monitor.json")
    with (
        patch.object(outbox_importer, "poll_once", return_value="idle") as replay,
        patch("apps.grain.scale.read_truck_scale_observation") as hardware,
    ):
        call_command("monitor_passage_scale", "--once")
    replay.assert_called_once()
    hardware.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("clear,age,accepted", [(False, 0, False), (True, 10, False), (True, 0, True)])
def test_collector_activation_requires_fresh_clear_then_survives_occupied_restart(tmp_path, monkeypatch, clear, age, accepted):
    import time
    from django.core.management import call_command, CommandError
    from apps.grain.models import PassageScaleAutomationState
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR", str(tmp_path))
    PassageScaleAutomationState.objects.get_or_create(scale_number="truck")
    box = Outbox(tmp_path)
    box.state("heartbeat", {"clear": clear, "armed": True, "updated_at": time.time() - age})
    if not accepted:
        with pytest.raises(CommandError):
            call_command("activate_weighbridge_collector")
        assert not outbox_importer.enabled()
    else:
        call_command("activate_weighbridge_collector")
        assert outbox_importer.enabled()
        box.state("heartbeat", {"clear": False, "armed": False, "updated_at": time.time()})
        call_command("activate_weighbridge_collector")
        assert outbox_importer.enabled()


@pytest.mark.django_db(transaction=True)
def test_recognized_entry_replays_original_photo_and_time_after_app_outage(tmp_path, settings):
    from apps.grain.models import Wagon
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.WEIGHING_AI_ENABLED = False
    value = _recognized_event("123ABC02", weight=4200, minutes=15)
    value["photo"] = b"\xff\xd8original entry"
    with patch("apps.grain.scale.read_truck_scale", side_effect=AssertionError("must not read next truck")):
        capture = outbox_importer.import_event(value)
        outbox_importer.import_event(value)
    assert capture.status == "completed", capture.error_code
    assert capture.action == "entry", capture.error_code
    wagon = Wagon.objects.get(pk=capture.wagon_id)
    assert wagon.number == "123ABC02" and wagon.gross_weight_kg == 4200
    record = wagon.weighings.get()
    assert record.photo.read() == value["photo"]
    assert record.created_at.isoformat() == value["stable_weight_at"]


@pytest.mark.django_db(transaction=True)
def test_independent_capture_cannot_be_duplicated_by_manual_hardware_button(tmp_path, monkeypatch, user_with_perms):
    from apps.grain import services, vehicle_weight_capture, statuses
    from apps.grain.models import Wagon, PassageWeightCapture
    from rest_framework.exceptions import ValidationError
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR", str(tmp_path))
    (tmp_path / "enabled").write_text("1")
    user = user_with_perms("collector-operator", codes=["grain.weigh"])
    wagon = Wagon.objects.create(direction="passage", workflow="simple", number="123ABC02", status=statuses.ARRIVED)
    with patch("apps.grain.scale.read_truck_scale") as hardware:
        with pytest.raises(ValidationError):
            services._prepare_manual_passage_scale_operation()
        with pytest.raises(ValidationError):
            vehicle_weight_capture._begin_capture(wagon_id=wagon.pk, action="entry", user=user, idempotency_key=uuid4(), now=timezone.now())
    hardware.assert_not_called()
    assert not PassageWeightCapture.objects.exists()


def test_brief_network_timeout_restarts_stability_without_missing_armed_truck():
    lane = Lane(stable_seconds=3)
    for t in range(3): lane.observe(scale_observation(0, t), t)
    assert not lane.observe(scale_observation(4200, 3), 3)
    lane.unavailable(4)
    assert lane.armed
    for t in (5, 6, 7): assert not lane.observe(scale_observation(4200, t), t)
    assert lane.observe(scale_observation(4200, 8), 8)
    lane.captured()
    lane.unavailable(9)
    for t in range(10, 15): assert not lane.observe(scale_observation(4200, t), t)


def test_repeated_short_failures_cannot_hide_a_long_observation_gap():
    lane = Lane(stable_seconds=2)
    for t in range(3): lane.observe(scale_observation(0, t), t)
    for t in range(3, 9):
        lane.observe(scale_observation(0, t, state="unavailable"), t)
    assert not lane.armed
    for t in range(9, 15): assert not lane.observe(scale_observation(4200, t), t)
    for t in range(15, 18): lane.observe(scale_observation(0, t), t)
    assert not lane.observe(scale_observation(4200, 18), 18)
    assert lane.observe(scale_observation(4200, 20), 20)


def captured_truck(lane, weight):
    for t in range(3): lane.observe(scale_observation(0, t), t)
    for t in (3, 4): assert not lane.observe(scale_observation(weight, t), t)
    assert lane.observe(scale_observation(weight, 5), 5)
    lane.captured()


def test_next_truck_on_a_scale_that_never_emptied_is_captured_after_the_platform_changes():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 3760)
    for t in range(6, 10): assert not lane.observe(scale_observation(3760, t), t)
    # The first truck drives off while the next one drives on: never empty.
    for t, weight in ((10, 1880), (11, 1700), (12, 3100)):
        assert not lane.observe(scale_observation(weight, t, state="unstable"), t)
    for t in (13, 14): assert not lane.observe(scale_observation(3680, t), t)
    assert lane.observe(scale_observation(3680, 15), 15)


def test_two_trucks_on_the_platform_at_once_rearm_on_the_rise():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 3760)
    for t, weight in ((6, 5600), (7, 5560)):
        assert not lane.observe(scale_observation(weight, t, state="unstable"), t)
    for t in (8, 9): assert not lane.observe(scale_observation(3680, t), t)
    assert lane.observe(scale_observation(3680, 10), 10)


def test_standing_truck_is_not_captured_again_by_changes_below_the_rearm_delta():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 4000)
    # A driver stepping out or a wheel on the ramp edge is not a new vehicle.
    for t, weight in enumerate((4600, 3300, 4600, 4600, 4600, 4600), start=6):
        assert not lane.observe(scale_observation(weight, t, state="unstable" if weight == 3300 else "ready"), t)


def test_one_glitched_reading_cannot_rearm_a_standing_truck():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 4000)
    assert not lane.observe(scale_observation(0, 6), 6)
    for t in range(7, 12): assert not lane.observe(scale_observation(4000, t), t)


def test_truck_stopping_half_off_the_scale_is_not_captured_again():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 3760)
    # Waiting at a barrier with only the rear axle on the platform.
    for t in range(6, 14): assert not lane.observe(scale_observation(1880, t), t)


def test_short_scale_outage_after_a_capture_keeps_watching_for_the_next_queued_truck():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 3760)
    for t in (6, 7): assert not lane.observe(scale_observation(3760, t), t)
    # The scale link drops for seven seconds; the queue keeps moving meanwhile.
    lane.unavailable(14)
    assert not lane.armed
    for t, weight in ((15, 1880), (16, 1700), (17, 3100)):
        assert not lane.observe(scale_observation(weight, t, state="unstable"), t)
    for t in (18, 19): assert not lane.observe(scale_observation(3680, t), t)
    assert lane.observe(scale_observation(3680, 20), 20)


def test_same_truck_standing_through_an_outage_is_not_captured_again():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 4000)
    lane.unavailable(14)
    for t in range(15, 25): assert not lane.observe(scale_observation(4000, t), t)
    assert not lane.armed


def test_outage_does_not_stitch_two_separate_glitches_into_a_rearm():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 4000)
    assert not lane.observe(scale_observation(5200, 6, state="unstable"), 6)
    lane.unavailable(14)
    assert not lane.observe(scale_observation(5200, 15, state="unstable"), 15)
    for t in range(16, 22): assert not lane.observe(scale_observation(4000, t), t)
    assert not lane.armed


def test_collector_captures_both_trucks_and_records_the_rearm(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    box = Outbox(tmp_path)
    box.state("config", {"stable_weight_seconds": 2})
    (tmp_path / "enabled").write_text("1")
    collector = Collector(box)
    clock = {"now": 1000.0}
    plan = [(0, "ready")] * 3 + [(3760, "ready")] * 4 + [(1880, "unstable"), (1700, "unstable"), (3100, "unstable")] \
        + [(3680, "ready")] * 3
    readings = iter(scale_observation(weight, second, state=state) for second, (weight, state) in enumerate(plan))
    strict = lambda *_: SimpleNamespace(weight_kg=Decimal(int(collector.lane.weight)), age_seconds=Decimal("0.1"),
                                        updated_at="sample")
    with patch("weighbridge.collector.time", fake_time(clock)), \
            patch("apps.grain.scale.read_truck_scale_observation", side_effect=lambda *_: next(readings)), \
            patch("apps.grain.scale.read_truck_scale", side_effect=strict), \
            patch.object(collector, "start_evidence"):
        for _ in plan:
            collector.poll()
            clock["now"] += 1
    collector.close()
    db = sqlite3.connect(box.path)
    weights = [int(__import__("json").loads(body)["weight_kg"]) for (body,) in db.execute("SELECT body FROM events ORDER BY seq")]
    assert weights == [3760, 3680]
    codes = [code for (code,) in db.execute("SELECT code FROM incidents ORDER BY id")]
    assert "rearmed_by_weight_change:3760->1700->3680" in codes


def _running_outbox(tmp_path, monkeypatch, settings):
    import time
    from apps.grain.models import PassageScaleAutomationState
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR", str(tmp_path))
    PassageScaleAutomationState.objects.get_or_create(scale_number="truck")
    box = Outbox(tmp_path)
    box.state("heartbeat", {"clear": True, "armed": True, "status": "running", "updated_at": time.time()})
    return box


@pytest.mark.django_db
def test_importer_writes_collector_config_only_when_it_changes(tmp_path, settings, monkeypatch):
    from apps.grain import passage_scale_automation
    box = _running_outbox(tmp_path, monkeypatch, settings)
    values = {"stable_weight_seconds": 3}
    monkeypatch.setattr(passage_scale_automation, "scale_automation_settings", lambda: dict(values))
    writes = []

    class Spy(Outbox):
        def state(self, key, value=None):
            if value is not None:
                writes.append(key)
            return super().state(key, value)

    monkeypatch.setattr(outbox_importer, "Outbox", Spy)
    assert outbox_importer.poll_once() == "idle"
    assert outbox_importer.poll_once() == "idle"
    assert writes == ["config"]
    values["stable_weight_seconds"] = 4
    assert outbox_importer.poll_once() == "idle"
    assert writes == ["config", "config"]
    assert box.state("config") == {"stable_weight_seconds": 4}


@pytest.mark.django_db
def test_importer_survives_a_locked_outbox_and_keeps_the_last_known_state(tmp_path, settings, monkeypatch, caplog):
    import logging
    _running_outbox(tmp_path, monkeypatch, settings)
    assert outbox_importer.poll_once() == "idle"
    with patch.object(Outbox, "next", side_effect=sqlite3.OperationalError("database is locked")):
        with caplog.at_level(logging.WARNING):
            assert outbox_importer.poll_once() == "idle"
    assert "locked" in caplog.text
    assert outbox_importer.poll_once() == "idle"


@pytest.mark.django_db
def test_importer_reports_unavailable_when_locked_before_any_heartbeat_was_read(tmp_path, settings, monkeypatch):
    _running_outbox(tmp_path, monkeypatch, settings)
    with patch.object(Outbox, "state", side_effect=sqlite3.OperationalError("database is locked")):
        assert outbox_importer.poll_once() == "unavailable"


@pytest.mark.django_db
def test_importer_reports_a_collector_outage_only_after_it_persists(tmp_path, settings, monkeypatch):
    import time
    box = _running_outbox(tmp_path, monkeypatch, settings)
    assert outbox_importer.poll_once() == "idle"
    box.state("heartbeat", {"clear": True, "armed": True, "status": "hardware_unavailable", "updated_at": time.time()})
    assert outbox_importer.poll_once() == "idle"
    box.state("heartbeat", {"clear": True, "armed": True, "status": "running", "updated_at": time.time()})
    assert outbox_importer.poll_once() == "idle"
    box.state("heartbeat", {"clear": True, "armed": True, "status": "running", "updated_at": time.time() - 15})
    assert outbox_importer.poll_once() == "idle"
    with patch.object(outbox_importer.time, "monotonic", return_value=time.monotonic() + outbox_importer.UNAVAILABLE_GRACE_SECONDS):
        assert outbox_importer.poll_once() == "unavailable"
    assert outbox_importer.poll_once() == "unavailable"
    box.state("heartbeat", {"clear": False, "armed": True, "status": "running", "updated_at": time.time()})
    assert outbox_importer.poll_once() == "candidate"


def test_collector_reports_a_scale_outage_only_after_five_seconds_of_failed_reads(tmp_path):
    import sqlite3
    box = Outbox(tmp_path)
    collector = Collector(box)
    clock = {"now": 1000.0}
    plan = [(0, "ready")] * 3 + [(0, "unavailable")] + [(0, "ready")] * 2 + [(0, "unavailable")] * 8 + [(0, "ready")]
    readings = iter(scale_observation(weight, second, state=state) for second, (weight, state) in enumerate(plan))
    statuses = []
    with patch("weighbridge.collector.time", fake_time(clock)), \
            patch("apps.grain.scale.read_truck_scale_observation", side_effect=lambda *_: next(readings)):
        for _ in plan:
            collector.poll()
            clock["now"] += 1
            statuses.append(collector.status)
    collector.close()
    assert statuses[:11] == ["running"] * 11  # one bad reading between good ones is not an outage
    assert statuses[11:14] == ["hardware_unavailable"] * 3 and statuses[14] == "running"
    codes = [code for (code,) in sqlite3.connect(box.path).execute("SELECT code FROM incidents ORDER BY id")]
    assert [code for code in codes if code in {"running", "hardware_unavailable"}] == ["running", "hardware_unavailable", "running"]


def test_outbox_writer_retains_fifo_order_across_a_busy_database(tmp_path):
    from weighbridge.writer import OutboxWriter
    box = Outbox(tmp_path)
    calls = []
    original_put = box.put
    def flaky_put(value):
        calls.append(value["id"])
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_put(value)
    box.put = flaky_put
    writer = OutboxWriter(box)
    first, second = outbox_event(), outbox_event()
    writer.enqueue("put", first)
    writer.enqueue("put", second)
    writer.start()
    assert writer.drain(timeout=5.0) is True
    assert calls == [first["id"], first["id"], second["id"]]
    assert box.counts()["total"] == 2
    writer.shutdown()


# --- Диагностика отказов ПК камер: сборщик сохраняет, импортёр читает ---

def _no_match_payload(**overrides):
    payload = {
        "status": "no_match", "retryable": False, "error": "vehicle number was not confirmed inside the ROI",
        "error_code": "plate_not_confirmed", "fresh_frames_seen": 24, "frames_scanned": 20, "detected_frames": 2,
        "ocr_candidates": 2, "accepted_reads": 2, "ambiguous_frames": 0, "confirmation_votes": 3,
        "best_detector_confidence": 0.29, "confirmation_window_seconds": 8.0, "votes": {"402BJG13": 2},
        "last_reads": [
            {"frame": 7, "variant": "plain", "raw_text": "402 BJG 13", "number": "402BJG13", "confidence": 0.81,
             "detector_confidence": 0.29, "bbox_w": 120, "bbox_h": 40, "crop_jpeg_base64": "/9j/AAAA"},
            {"frame": 11, "variant": "clahe", "raw_text": "402BJG13", "number": "402BJG13", "confidence": 0.77,
             "detector_confidence": 0.26, "bbox_w": 118, "bbox_h": 39},
        ],
        "frames": ["/9j/base64frame"], "orientation": {"label": "rear", "confidence": 0.97},
    }
    payload.update(overrides)
    return payload


def _collector_ocr(tmp_path, side_effect, *, photo=None, minutes=0, weight=4200):
    from datetime import timedelta
    stable_at = (timezone.now() - timedelta(minutes=minutes)).isoformat()
    collector, box, value = collector_event(tmp_path, weight_kg=weight, stable_weight_at=stable_at)
    with patch("apps.cameras.ai.recognize_vehicle_from_camera", side_effect=side_effect):
        collector.recognize(value)
    collector.close()
    box.finish(value["id"], "photo", photo=photo)
    stored = box.next()
    box.ack(value["id"])
    return stored


def test_collector_keeps_no_match_diagnostics_without_frames(tmp_path):
    import json
    from apps.cameras import ai
    stored = _collector_ocr(tmp_path, ai.AiError(422, "not confirmed", _no_match_payload()))
    assert stored["recognition"] is None
    assert stored["recognition_error"] == "no_match"
    assert stored["orientation"] == "rear" and stored["recognition_frame_bound"] is True
    diagnostics = stored["recognition_diagnostics"]
    assert diagnostics["status"] == "no_match" and diagnostics["error_code"] == "plate_not_confirmed"
    assert diagnostics["votes"] == {"402BJG13": 2}
    assert (diagnostics["detected_frames"], diagnostics["frames_scanned"], diagnostics["confirmation_votes"]) == (2, 20, 3)
    assert diagnostics["best_detector_confidence"] == 0.29 and diagnostics["confirmation_window_seconds"] == 8.0
    assert [read["frame"] for read in diagnostics["last_reads"]] == [7, 11]
    assert set(diagnostics["last_reads"][0]) == {
        "frame", "variant", "raw_text", "number", "confidence", "detector_confidence", "bbox_w", "bbox_h",
    }
    assert not {"frames", "orientation", "retryable"} & set(diagnostics)
    assert "base64" not in json.dumps(diagnostics)


def test_collector_bounds_oversized_diagnostics(tmp_path):
    from apps.cameras import ai
    from apps.grain import plate_recognition
    from weighbridge import collector
    # One vote ceiling on both sides: what the collector keeps, the CRM keeps whole.
    assert collector.MAX_DIAGNOSTIC_VOTES == plate_recognition.MAX_NO_MATCH_VOTES == 8
    payload = _no_match_payload(
        votes={f"{n:03d}ABC13": 1 for n in range(15)},
        last_reads=[{"frame": n, "raw_text": "x" * 100, "confidence": 2.5, "bbox_w": -1} for n in range(12)],
        error="e" * 500, detected_frames=-3, confirmation_votes=True, best_detector_confidence="0.5",
    )
    diagnostics = _collector_ocr(tmp_path, ai.AiError(422, "not confirmed", payload))["recognition_diagnostics"]
    assert len(diagnostics["votes"]) == 8 and len(diagnostics["last_reads"]) == 8
    assert diagnostics["last_reads"][0] == {"frame": 0, "raw_text": "x" * 30}
    assert len(diagnostics["error"]) == 300
    assert "detected_frames" not in diagnostics and "confirmation_votes" not in diagnostics
    assert "best_detector_confidence" not in diagnostics


@pytest.mark.parametrize("status,expected", [
    ("camera_unavailable", "camera_unavailable"), ("stale_weight_trigger", "stale_weight_trigger"),
    ("Failed", "failed"), ("<html>", "html"), ("", "recognition_unavailable"), (None, "recognition_unavailable"),
    (500, "recognition_unavailable"), ("a" * 100, "a" * 64),
])
def test_collector_reports_the_camera_status_as_the_recognition_error(tmp_path, status, expected):
    from apps.cameras import ai
    payload = {"status": status, "error": "camera offline", "orientation": {"label": "front", "confidence": 0.9}}
    stored = _collector_ocr(tmp_path, ai.AiError(503, "camera offline", payload))
    assert stored["recognition_error"] == expected
    assert stored["orientation"] == "front" and stored["recognition_frame_bound"] is True
    assert stored["recognition_diagnostics"]["error"] == "camera offline"
    assert stored["recognition_diagnostics"].get("status") == (status if isinstance(status, str) else None)


def test_collector_drops_a_refusal_that_arrives_after_the_truck_left(tmp_path):
    from apps.cameras import ai
    collector, box, value = collector_event(tmp_path)

    def late(*args, **kwargs):
        collector.current = str(uuid4())
        raise ai.AiError(422, "not confirmed", _no_match_payload())

    with patch("apps.cameras.ai.recognize_vehicle_from_camera", side_effect=late):
        collector.recognize(value)
    collector.close()
    box.finish(value["id"], "photo")
    stored = box.next()
    assert stored["recognition_error"] == "recognition_unavailable" and stored["recognition_frame_bound"] is False
    assert stored["orientation"] == "" and stored["recognition_diagnostics"] is None


def test_collector_outage_keeps_the_old_error_without_diagnostics(tmp_path):
    from apps.cameras import ai
    stored = _collector_ocr(tmp_path, ai.AiUnavailable("timed out"))
    assert stored["recognition"] is None and stored["recognition_error"] == "recognition_unavailable"
    assert stored["recognition_frame_bound"] is False and stored["orientation"] == ""
    assert not stored.get("recognition_diagnostics")


@pytest.fixture
def ai_import(identity_ai):
    """Сборщик включён, номер без подтверждения сверяется по снимку через ИИ."""
    identity_ai.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True


def _import_failure(tmp_path, failure, *, minutes=1, weight=8500):
    """Import one collector event whose camera answer failed; also return the status recorded before apply."""
    from apps.grain import passage_scale_automation as automation
    stored = _collector_ocr(tmp_path / str(uuid4()), failure, photo=JPEG, minutes=minutes, weight=weight)
    staged, apply = {}, automation._apply_recognized_capture

    def observed_apply(capture_id):
        staged["response_status"] = AutomaticPassageCapture.objects.get(pk=capture_id).response_status
        return apply(capture_id)

    with patch.object(automation, "_apply_recognized_capture", side_effect=observed_apply):
        return outbox_importer.import_event(stored), staged["response_status"]


def _import_no_match(tmp_path, *, minutes=1, weight=8500, **overrides):
    from apps.cameras import ai
    failure = ai.AiError(422, "not confirmed", _no_match_payload(**overrides))
    return _import_failure(tmp_path, failure, minutes=minutes, weight=weight)


@pytest.mark.django_db(transaction=True)
def test_import_of_no_match_keeps_diagnostics_and_the_single_weak_plate(tmp_path, ai_import):
    from apps.grain import services
    capture, staged = _import_no_match(tmp_path)
    assert capture.status == "completed" and capture.action == services.AUTO_ACTION_UNASSIGNED
    assert capture.plate_unresolved and capture.error_code == "collector_plate_unresolved"
    assert capture.error_detail == "Номер не подтверждён: 2 голоса за 402BJG13 (нужно 3)"
    assert staged == 422 and capture.response_status == 200  # 200 = applied, as every finished capture
    assert capture.vehicle_number == "402BJG13" and capture.confirmation_votes == 2
    assert capture.ai_payload_json["weak_plate"] is True
    assert capture.ai_payload_json["votes"] == {"402BJG13": 2}
    assert capture.ai_payload_json["last_reads"][0]["number"] == "402BJG13"
    assert "frames" not in capture.ai_payload_json
    item = UnassignedWeighing.objects.get()
    assert item.vehicle_number == "402BJG13" and item.orientation == "rear"
    assert item.reason == "identity_verification_required" and item.capture_id == capture.pk


@pytest.mark.django_db(transaction=True)
def test_import_of_no_match_with_competing_plates_keeps_no_number(tmp_path, ai_import):
    capture, _ = _import_no_match(tmp_path, votes={"402BJG13": 2, "402BJG18": 1})
    assert capture.vehicle_number == "" and capture.confirmation_votes is None
    assert "weak_plate" not in capture.ai_payload_json
    assert capture.ai_payload_json["votes"] == {"402BJG13": 2, "402BJG18": 1}
    assert capture.error_detail == "Номер не подтверждён: 2 голоса за 402BJG13, 1 голос за 402BJG18 (нужно 3)"
    assert UnassignedWeighing.objects.get().vehicle_number == ""


@pytest.mark.django_db(transaction=True)
def test_import_of_no_match_with_a_single_vote_or_a_malformed_plate_keeps_no_number(tmp_path, ai_import):
    capture, _ = _import_no_match(tmp_path, votes={"402BJG13": 1})
    assert capture.vehicle_number == "" and "weak_plate" not in capture.ai_payload_json
    capture, _ = _import_no_match(tmp_path, votes={"MERCEDES": 2})
    assert capture.vehicle_number == "" and "weak_plate" not in capture.ai_payload_json
    assert capture.error_detail == "Номер не подтверждён: 2 голоса за MERCEDES (нужно 3)"


@pytest.mark.django_db(transaction=True)
def test_import_of_no_match_with_an_impossible_vote_count_keeps_no_number(tmp_path, ai_import):
    # 40 000 votes fit the JSON tally but not the capture's smallint column:
    # the event still imports, and the tally alone names nobody.
    capture, staged = _import_no_match(tmp_path, votes={"402BJG13": 40000})
    assert capture.status == "completed" and capture.error_code == "collector_plate_unresolved" and staged == 422
    assert capture.vehicle_number == "" and capture.confirmation_votes is None
    assert "weak_plate" not in capture.ai_payload_json
    assert capture.ai_payload_json["votes"] == {"402BJG13": 40000}
    assert capture.error_detail == "Номер не подтверждён: 40000 голосов за 402BJG13 (нужно 3)"
    assert UnassignedWeighing.objects.get().vehicle_number == ""


@pytest.mark.django_db(transaction=True)
def test_import_does_not_trust_a_weak_plate_whose_competitor_was_trimmed(tmp_path, ai_import):
    from apps.cameras import ai
    stored = _collector_ocr(tmp_path / str(uuid4()), ai.AiError(422, "not confirmed", _no_match_payload()),
                            photo=JPEG, minutes=1, weight=8500)
    # A longer tally than the CRM keeps (an older collector, a chattier Camera-PC):
    # the competing number is the ninth entry and would be cut on import.
    stored["recognition_diagnostics"]["votes"] = {"402BJG13": 2, **{f"{n:03d}ABC13": 0 for n in range(7)}, "402BJG18": 1}
    capture = outbox_importer.import_event(stored)
    assert capture.status == "completed" and capture.error_code == "collector_plate_unresolved"
    assert capture.vehicle_number == "" and capture.confirmation_votes is None
    assert "weak_plate" not in capture.ai_payload_json
    assert len(capture.ai_payload_json["votes"]) == 8 and "402BJG18" not in capture.ai_payload_json["votes"]
    assert UnassignedWeighing.objects.get().vehicle_number == ""


@pytest.mark.django_db(transaction=True)
def test_import_of_no_match_without_a_detected_plate_explains_the_detector_miss(tmp_path, ai_import):
    capture, staged = _import_no_match(tmp_path, votes={}, last_reads=[], detected_frames=0, ocr_candidates=0)
    assert capture.vehicle_number == "" and staged == 422
    assert capture.error_detail == "Камера не нашла табличку: 0 из 20 кадров"
    assert capture.ai_payload_json["detected_frames"] == 0 and "votes" not in capture.ai_payload_json


@pytest.mark.django_db(transaction=True)
def test_import_of_no_match_keeps_the_zoom_counters_and_says_the_zoom_found_nothing_either(tmp_path, ai_import):
    from apps.cameras import ai
    refusal = ai.AiError(422, "not confirmed", _no_match_payload(votes={}, last_reads=[], detected_frames=0, ocr_candidates=0))
    stored = _collector_ocr(tmp_path / str(uuid4()), refusal, photo=JPEG, minutes=1, weight=8500)
    # The Camera-PC also searched every frame in zoomed tiles and found no plate
    # there either; a collector that forwards those counters lets the CRM say so.
    stored["recognition_diagnostics"].update({"zoom_frames": 12, "zoom_detected_frames": 0, "zoom_tiles": 4})
    capture = outbox_importer.import_event(stored)
    assert capture.error_code == "collector_plate_unresolved"
    assert capture.error_detail == "Камера не нашла табличку: 0 из 20 кадров, зум по тайлам тоже пуст"
    assert capture.ai_payload_json["zoom_frames"] == 12 and capture.ai_payload_json["zoom_detected_frames"] == 0
    assert "zoom_tiles" not in capture.ai_payload_json  # only the two counters are kept, not an arbitrary key
    # A refusal that never zoomed keeps the plain wording.
    stored = _collector_ocr(tmp_path / str(uuid4()), refusal, photo=JPEG, minutes=1, weight=8500)
    stored["recognition_diagnostics"].update({"zoom_frames": 0, "zoom_detected_frames": -1})
    capture = outbox_importer.import_event(stored)
    assert capture.error_detail == "Камера не нашла табличку: 0 из 20 кадров"
    assert capture.ai_payload_json["zoom_frames"] == 0 and "zoom_detected_frames" not in capture.ai_payload_json


@pytest.mark.django_db(transaction=True)
def test_import_of_a_camera_failure_keeps_its_status_without_a_no_match_code(tmp_path, ai_import):
    from apps.cameras import ai
    payload = {"status": "camera_unavailable", "error": "rtsp offline", "orientation": {"label": "rear", "confidence": 0.9}}
    capture, staged = _import_failure(tmp_path, ai.AiError(503, "rtsp offline", payload))
    assert capture.error_code == "collector_plate_unresolved" and capture.error_detail == "Камера: camera_unavailable"
    assert staged is None and capture.vehicle_number == "" and capture.orientation == "rear"
    assert capture.ai_payload_json == {"status": "camera_unavailable", "error": "rtsp offline"}


@pytest.mark.django_db(transaction=True)
def test_import_of_a_stale_trigger_names_the_status_not_a_detector_miss(tmp_path, ai_import):
    from apps.cameras import ai
    # Zero detected frames on a refusal that never looked for a plate is not a detector miss.
    payload = {"status": "stale_weight_trigger", "error": "weight trigger is stale", "detected_frames": 0,
               "frames_scanned": 20, "orientation": {"label": "rear", "confidence": 0.9}}
    capture, staged = _import_failure(tmp_path, ai.AiError(409, "stale trigger", payload))
    assert capture.error_code == "collector_plate_unresolved" and capture.error_detail == "Камера: stale_weight_trigger"
    assert staged is None and capture.vehicle_number == ""
    assert capture.ai_payload_json["detected_frames"] == 0 and capture.ai_payload_json["frames_scanned"] == 20


@pytest.mark.django_db(transaction=True)
def test_import_of_an_old_event_without_diagnostics_is_unchanged(tmp_path, ai_import):
    from apps.cameras import ai
    capture, staged = _import_failure(tmp_path, ai.AiUnavailable("timed out"))
    assert capture.error_code == "collector_plate_unresolved"
    assert capture.error_detail == "Вес сохранён сборщиком; номер требует проверки."
    assert capture.ai_payload_json == {} and staged is None and capture.vehicle_number == ""


def _recognized_event(number, *, weight, minutes, orientation="front"):
    from datetime import timedelta
    value = outbox_event()
    value["weight_kg"] = weight
    value["stable_weight_at"] = (timezone.now() - timedelta(minutes=minutes)).isoformat()
    value["photo"] = b"\xff\xd8\xff\xe0front frame"
    value["recognition"] = {
        "vehicle_number": number, "source": "main", "recognized_at": value["stable_weight_at"],
        "stable_weight_at": value["stable_weight_at"], "orientation": {"label": orientation, "confidence": 0.99},
        "confirmation": {"votes": 3, "detector_confidence": 0.95, "ocr_confidence": 0.96},
    }
    return value


@pytest.mark.django_db(transaction=True)
def test_weak_rear_plate_of_a_truck_on_site_books_the_exit_without_gpt(tmp_path, ai_import):
    from apps.grain import statuses as st, weighing_identity as identity
    from apps.grain.models import Wagon
    entry = outbox_importer.import_event(_recognized_event("402BJG13", weight=4200, minutes=60))
    identity.process_once()
    visit = Wagon.objects.get(number="402BJG13")
    assert visit.status == st.AT_SILO and visit.gross_weight_kg == 4200
    capture, _ = _import_no_match(tmp_path)
    assert capture.vehicle_number == "402BJG13"
    process_without_gpt()
    departure = UnassignedWeighing.objects.get(capture=capture)
    assert departure.status == "assigned" and departure.action == "exit" and departure.wagon_id == visit.pk
    assert departure.identity_check.status == "matched"
    assert departure.identity_check.evidence["identity_source"] == "ocr"
    assert departure.identity_check.evidence["original_number"] == "402BJG13"
    visit.refresh_from_db()
    assert visit.status == st.COMPLETED and visit.tare_weight_kg == 8500 and visit.net_weight_kg == 4300
    assert UnassignedWeighing.objects.get(capture=entry).wagon_id == visit.pk


@pytest.mark.django_db(transaction=True)
def test_weak_rear_plate_without_a_truck_on_site_still_reads_the_frame(tmp_path, ai_import):
    from apps.grain import weighing_identity as identity
    capture, _ = _import_no_match(tmp_path)
    verdict = {"exit": {"plate": "", "plate_clear": False, "orientation": "rear"}}
    with patch.object(identity, "request_verification", return_value=(verdict, "response-test")) as request:
        identity.process_once()
    assert request.call_count == 1
    departure = UnassignedWeighing.objects.get(capture=capture)
    assert departure.status == "open" and departure.identity_check.reason == "plate_unreadable"
    assert departure.identity_check.evidence["original_number"] == "402BJG13"


@pytest.mark.django_db(transaction=True)
def test_weak_front_plate_always_reads_the_frame_before_opening_a_visit(tmp_path, ai_import):
    from apps.grain import statuses as st, weighing_identity as identity
    from apps.grain.models import Wagon
    capture, _ = _import_no_match(tmp_path, weight=4200, orientation={"label": "front", "confidence": 0.95})
    assert capture.vehicle_number == "402BJG13" and capture.orientation == "front"
    verdict = {"exit": {"plate": "402BJG13", "plate_clear": True, "orientation": "front"}}
    with patch.object(identity, "request_verification", return_value=(verdict, "response-test")) as request:
        identity.process_once()
    assert request.call_count == 1
    arrival = UnassignedWeighing.objects.get(capture=capture)
    assert arrival.status == "assigned" and arrival.action == "entry"
    assert arrival.identity_check.evidence["identity_source"] == "gpt"
    assert Wagon.objects.get(number="402BJG13").status == st.AT_SILO


@pytest.mark.django_db(transaction=True)
def test_weak_rear_plate_booking_is_flagged_in_the_journal(tmp_path, ai_import):
    from apps.eventlog.models import EventLog
    from apps.grain import weighing_identity as identity
    entry = outbox_importer.import_event(_recognized_event("402BJG13", weight=4200, minutes=60))
    identity.process_once()
    arrival = EventLog.objects.get(event_type="grain_identity_verified")
    assert arrival.payload["weak_plate"] is False
    capture, _ = _import_no_match(tmp_path)
    process_without_gpt()
    departure = UnassignedWeighing.objects.get(capture=capture)
    assert departure.status == "assigned" and departure.action == "exit"
    booked = EventLog.objects.get(event_type="grain_identity_verified", payload__check_id=departure.identity_check.pk)
    assert booked.payload["weak_plate"] is True and booked.payload["source"] == "ocr"
    assert booked.payload["verified_number"] == "402BJG13" and booked.payload["weight_kg"] == 8500
    assert EventLog.objects.get(event_type="grain_identity_verified", payload__weight_kg=4200).payload["check_id"] \
        == UnassignedWeighing.objects.get(capture=entry).identity_check.pk


@pytest.mark.django_db(transaction=True)
def test_import_of_an_interrupted_search_names_its_status_and_trusts_no_weak_plate(tmp_path, ai_import):
    from apps.cameras import ai
    # Two votes gathered before the search was cut short never saw the whole
    # window: the status leads, the tally is context, no plate is taken.
    payload = _no_match_payload(status="interrupted", error="shared inference timed out")
    capture, staged = _import_failure(tmp_path, ai.AiError(503, "shared inference timed out", payload))
    assert capture.error_code == "collector_plate_unresolved"
    assert capture.error_detail == "Камера: interrupted; голоса: 2 голоса за 402BJG13"
    assert staged is None and capture.vehicle_number == "" and capture.confirmation_votes is None
    assert "weak_plate" not in capture.ai_payload_json and capture.ai_payload_json["votes"] == {"402BJG13": 2}
    assert UnassignedWeighing.objects.get().vehicle_number == ""


@pytest.mark.django_db(transaction=True)
def test_collector_marks_a_tally_it_had_to_cut_and_the_importer_trusts_no_weak_plate(tmp_path, ai_import):
    from apps.cameras import ai
    from weighbridge import collector as collector_module
    # Ten readings, the competitor last: the collector keeps eight but says so.
    votes = {"402BJG13": 2, **{f"{n:03d}ABC13": 0 for n in range(8)}, "402BJG18": 1}
    stored = _collector_ocr(tmp_path / str(uuid4()), ai.AiError(422, "not confirmed", _no_match_payload(votes=votes)),
                            photo=JPEG, minutes=1, weight=8500)
    diagnostics = stored["recognition_diagnostics"]
    assert len(diagnostics["votes"]) == collector_module.MAX_DIAGNOSTIC_VOTES and diagnostics["votes_truncated"] is True
    capture = outbox_importer.import_event(stored)
    assert capture.vehicle_number == "" and capture.confirmation_votes is None
    assert "weak_plate" not in capture.ai_payload_json and capture.ai_payload_json["votes_truncated"] is True
    # A malformed entry never hides a competitor: filtering happens before the cut.
    kept = collector_module._diagnostics(_no_match_payload(votes={"402BJG13": 2, "BAD": True, "402BJG18": 1}))
    assert kept["votes"] == {"402BJG13": 2, "402BJG18": 1} and "votes_truncated" not in kept
