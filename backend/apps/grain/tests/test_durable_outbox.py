from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.grain.scale import ScaleObservation
from apps.grain import outbox_importer
from apps.grain.models import AutomaticPassageCapture, UnassignedWeighing, WeighingPhotoDelivery
from weighbridge.outbox import Outbox, Lane
from weighbridge.collector import Collector


def observation(weight, second, *, state="ready"):
    return ScaleObservation(state, Decimal(weight), True, state=="ready", False, Decimal("0.1"), str(second))


def test_stable_truck_after_moving_is_captured_once_then_next_after_clear():
    lane=Lane(stable_seconds=2)
    for t in range(3): assert not lane.observe(observation(0,t),t)
    assert not lane.observe(observation(2000,3,state="unstable"),3)
    assert not lane.observe(observation(4000,4),4)
    assert not lane.observe(observation(4000,5),5)
    assert lane.observe(observation(4000,6),6)
    lane.captured()
    for t in range(7,20): assert not lane.observe(observation(4000,t),t)
    for t in range(20,23): assert not lane.observe(observation(0,t),t)
    assert not lane.observe(observation(4200,23),23)
    assert lane.observe(observation(4200,25),25)


def test_restart_gap_or_duplicate_reading_cannot_invent_a_new_occupancy():
    lane=Lane(stable_seconds=2)
    for t in range(10): assert not lane.observe(observation(4000,t),t)
    for t in range(10,13): lane.observe(observation(0,t),t)
    for t in range(13,17): assert not lane.observe(observation(4000,13),t)
    lane.gap()
    for t in range(17,25): assert not lane.observe(observation(4000,t),t)


def event():
    return {"id":str(uuid4()),"version":1,"weight_kg":4200,"camera":"cam1",
            "stable_weight_at":timezone.now().isoformat(),"scale_age_seconds":"0.1","scale_updated_at":"sample"}


def test_queue_restart_order_immutable_evidence_and_ack(tmp_path):
    box=Outbox(tmp_path); a,b=event(),event()
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
    box=Outbox(tmp_path);value=event();box.put(value)
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
    box=Outbox(tmp_path);value=event();box.put(value)
    collector=Collector(box)
    import time
    collector.current=value["id"];collector.last_good=time.monotonic()
    def camera(*args, **kwargs):
        collector.current=str(uuid4())
        return {"vehicle_number":"123ABC13", "orientation":{"label":"rear","confidence":1}}
    with patch("apps.cameras.ai.recognize_vehicle_from_camera",side_effect=camera):
        collector.recognize(value)
    box.finish(value["id"],"photo")
    assert box.next()["recognition"] is None
    assert box.next()["orientation"] == ""
    assert box.next()["recognition_error"] == "recognition_after_departure"
    collector.pool.shutdown()


def test_camera_failure_cannot_lose_already_persisted_weight(tmp_path):
    box=Outbox(tmp_path);value=event();box.put(value)
    collector=Collector(box)
    import time
    collector.current=value["id"];collector.last_good=time.monotonic()
    with patch("apps.grain.scale._open_request",side_effect=TimeoutError):
        collector.snapshot(value)
    box.finish(value["id"],"ocr")
    assert box.next()["weight_kg"] == 4200
    assert box.next()["photo"] is None
    collector.pool.shutdown()


@pytest.mark.django_db(transaction=True)
def test_database_commit_before_ack_is_idempotent_after_crash(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT=tmp_path/"media"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED=True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=True
    settings.WEIGHING_AI_ENABLED=False
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR",str(tmp_path))
    box=Outbox(tmp_path);value=event();box.put(value)
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
    box=Outbox(tmp_path);value=event();box.put(value);box.finish(value["id"],"photo",photo=b"photo");box.finish(value["id"],"ocr")
    with patch.object(AutomaticPassageCapture.objects,"get_or_create",side_effect=OSError("database offline")):
        with pytest.raises(OSError): outbox_importer.import_event(box.next())
    assert box.next()["photo"] == b"photo" and box.counts()["pending"] == 1


@pytest.mark.django_db(transaction=True)
def test_enabled_monitor_restart_replays_without_touching_hardware(tmp_path, settings, monkeypatch):
    from django.core.management import call_command
    from apps.grain import passage_scale_automation, passage_monitor
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR", str(tmp_path))
    (tmp_path / "enabled").write_text("1")
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(tmp_path / "monitor.json")
    with (
        patch.object(outbox_importer, "poll_once", return_value=passage_scale_automation.MonitorIteration(state="idle")) as replay,
        patch.object(passage_scale_automation, "monitor_once") as legacy,
        patch.object(passage_monitor, "prepare_start") as reset,
    ):
        call_command("monitor_passage_scale", "--once")
    replay.assert_called_once()
    legacy.assert_not_called()
    reset.assert_not_called()


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
    from datetime import timedelta
    from apps.grain.models import Wagon
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_EXPORT_ENABLED = False
    settings.WEIGHING_AI_ENABLED = False
    value = event()
    value["stable_weight_at"] = (timezone.now() - timedelta(minutes=15)).isoformat()
    value["photo"] = b"\xff\xd8original entry"
    value["recognition"] = {
        "vehicle_number": "123ABC02", "source": "main",
        "recognized_at": value["stable_weight_at"],
        "stable_weight_at": value["stable_weight_at"],
        "orientation": {"label": "front", "confidence": 0.99},
        "confirmation": {"votes": 3, "detector_confidence": 0.95, "ocr_confidence": 0.96},
    }
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
