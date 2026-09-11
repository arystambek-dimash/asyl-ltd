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
    collector.close()
    box.finish(value["id"],"photo")
    assert box.next()["recognition"] is None
    assert box.next()["orientation"] == ""
    assert box.next()["recognition_error"] == "recognition_after_departure"
    collector.close()


def test_camera_failure_cannot_lose_already_persisted_weight(tmp_path):
    box=Outbox(tmp_path);value=event();box.put(value)
    collector=Collector(box)
    import time
    collector.current=value["id"];collector.last_good=time.monotonic()
    with patch("apps.grain.scale._open_request",side_effect=TimeoutError):
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
    for t in range(3): lane.observe(observation(0, t), t)
    assert not lane.observe(observation(4200, 3), 3)
    lane.unavailable(4)
    assert lane.armed
    for t in (5, 6, 7): assert not lane.observe(observation(4200, t), t)
    assert lane.observe(observation(4200, 8), 8)
    lane.captured()
    lane.unavailable(9)
    for t in range(10, 15): assert not lane.observe(observation(4200, t), t)


def test_repeated_short_failures_cannot_hide_a_long_observation_gap():
    lane = Lane(stable_seconds=2)
    for t in range(3): lane.observe(observation(0, t), t)
    for t in range(3, 9):
        lane.observe(observation(0, t, state="unavailable"), t)
    assert not lane.armed
    for t in range(9, 15): assert not lane.observe(observation(4200, t), t)
    for t in range(15, 18): lane.observe(observation(0, t), t)
    assert not lane.observe(observation(4200, 18), 18)
    assert lane.observe(observation(4200, 20), 20)


def captured_truck(lane, weight):
    for t in range(3): lane.observe(observation(0, t), t)
    for t in (3, 4): assert not lane.observe(observation(weight, t), t)
    assert lane.observe(observation(weight, 5), 5)
    lane.captured()


def test_next_truck_on_a_scale_that_never_emptied_is_captured_after_the_platform_changes():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 3760)
    for t in range(6, 10): assert not lane.observe(observation(3760, t), t)
    # The first truck drives off while the next one drives on: never empty.
    for t, weight in ((10, 1880), (11, 1700), (12, 3100)):
        assert not lane.observe(observation(weight, t, state="unstable"), t)
    for t in (13, 14): assert not lane.observe(observation(3680, t), t)
    assert lane.observe(observation(3680, 15), 15)


def test_two_trucks_on_the_platform_at_once_rearm_on_the_rise():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 3760)
    for t, weight in ((6, 5600), (7, 5560)):
        assert not lane.observe(observation(weight, t, state="unstable"), t)
    for t in (8, 9): assert not lane.observe(observation(3680, t), t)
    assert lane.observe(observation(3680, 10), 10)


def test_standing_truck_is_not_captured_again_by_changes_below_the_rearm_delta():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 4000)
    # A driver stepping out or a wheel on the ramp edge is not a new vehicle.
    for t, weight in enumerate((4600, 3300, 4600, 4600, 4600, 4600), start=6):
        assert not lane.observe(observation(weight, t, state="unstable" if weight == 3300 else "ready"), t)


def test_one_glitched_reading_cannot_rearm_a_standing_truck():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 4000)
    assert not lane.observe(observation(0, 6), 6)
    for t in range(7, 12): assert not lane.observe(observation(4000, t), t)


def test_truck_stopping_half_off_the_scale_is_not_captured_again():
    lane = Lane(stable_seconds=2)
    captured_truck(lane, 3760)
    # Waiting at a barrier with only the rear axle on the platform.
    for t in range(6, 14): assert not lane.observe(observation(1880, t), t)


def test_collector_captures_both_trucks_and_records_the_rearm(tmp_path):
    import sqlite3
    import time as real_time
    from types import SimpleNamespace
    box = Outbox(tmp_path)
    box.state("config", {"stable_weight_seconds": 2})
    (tmp_path / "enabled").write_text("1")
    collector = Collector(box)
    clock = {"now": 1000.0}
    plan = [(0, "ready")] * 3 + [(3760, "ready")] * 4 + [(1880, "unstable"), (1700, "unstable"), (3100, "unstable")] \
        + [(3680, "ready")] * 3
    readings = iter(observation(weight, second, state=state) for second, (weight, state) in enumerate(plan))
    fake_time = SimpleNamespace(monotonic=lambda: clock["now"], time=real_time.time, sleep=real_time.sleep)
    strict = lambda *_: SimpleNamespace(weight_kg=Decimal(int(collector.lane.weight)), age_seconds=Decimal("0.1"),
                                        updated_at="sample")
    with patch("weighbridge.collector.time", fake_time), \
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
