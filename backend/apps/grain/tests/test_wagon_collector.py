import http.client
import json
import sqlite3
import threading
import time as real_time
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.grain import scale
from apps.grain.tests.factories import JPEG, fake_time, scale_observation
from apps.grain.tests.test_auto_arrival import wagon_reply
from weighbridge.outbox import Outbox
from weighbridge.wagon_collector import WagonCollector
from weighbridge.wagon_stop import StopTracker


def motion(state, still_seconds=0.0, age=0.3):
    return {"state": state, "still_seconds": still_seconds, "sample_age_seconds": age}


def stop(weight=62340):
    return {"id": "stop-1", "weight_kg": weight, "scale_updated_at": "t5"}


def test_arrival_needs_ten_still_seconds_and_a_stable_full_weight():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    assert tracker.observe(scale_observation(62340, 1), motion("moving"), 1) is None
    for t in range(2, 6):  # still, but not yet for ten seconds
        assert tracker.observe(scale_observation(62340, t), motion("still", t - 1), t) is None
    assert tracker.observe(scale_observation(62340, 12), motion("still", 11), 12) is None   # stability window starts
    assert tracker.observe(scale_observation(62340, 13), motion("still", 12), 13) is None
    assert tracker.observe(scale_observation(62340, 14), motion("still", 13), 14) == ("arrival", 62340.0)


def test_unstable_or_light_weight_and_unknown_motion_do_not_arrive():
    tracker = StopTracker(still_seconds=10, stable_seconds=2, empty_max=1000)
    for t in range(12, 20):
        assert tracker.observe(scale_observation(62340, t, stable=False), motion("still", 20), t) is None
    for t in range(20, 25):
        assert tracker.observe(scale_observation(800, t), motion("still", 30), t) is None
    for t in range(25, 30):
        assert tracker.observe(scale_observation(62340, t), None, t) is None
    for t in range(30, 35):
        assert tracker.observe(scale_observation(62340, t), motion("still", 40, age=9.0), t) is None
    assert tracker.observe(scale_observation(62340, 35), motion("still", 45), 35) is None
    assert tracker.observe(scale_observation(62340, 37), motion("still", 47), 37) == ("arrival", 62340.0)


def test_departure_reports_the_last_stable_weight_before_motion():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    tracker.arrived(stop(), 14)
    assert tracker.standing["id"] == "stop-1"
    for t, weight, stable in ((20, 50000, True), (21, 40200, False), (22, 24120, True), (23, 24080, False)):
        assert tracker.observe(scale_observation(weight, t, stable=stable), motion("still", t), t) is None
    result = tracker.observe(scale_observation(23990, 24, stable=False), motion("moving"), 24)
    assert result == ("departure", stop(), 24120, False)
    assert tracker.standing is None and tracker.last_stable is None


def test_a_single_unknown_motion_poll_does_not_flag_a_motion_gap():
    # One silent poll from the camera PC is not evidence that the wagon left
    # unseen; only unknown motion persisting beyond motion_max_age is.
    tracker = StopTracker(still_seconds=10, stable_seconds=2, motion_max_age=5)
    tracker.arrived(stop(), 14)
    assert tracker.observe(scale_observation(24120, 15), motion("still", 15), 15) is None
    assert tracker.observe(scale_observation(24120, 16), None, 16) is None
    assert tracker.motion_gap is False
    assert tracker.observe(scale_observation(24120, 17), motion("still", 17), 17) is None
    assert tracker.motion_gap is False
    assert tracker.observe(scale_observation(24120, 18), motion("moving"), 18) == ("departure", stop(), 24120, False)


def test_departure_keeps_the_last_stable_reading_for_the_exit_timestamps():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    tracker.arrived(stop(), 14)
    assert tracker.observe(scale_observation(24120, 20), motion("still", 20), 20) is None
    departing = scale_observation(23990, 21, stable=False)
    assert tracker.observe(departing, motion("moving"), 21) == ("departure", stop(), 24120, False)
    assert tracker.last_exit == (24120, "t20", 20)


def test_departure_without_any_stable_weight_reports_none_and_flags_a_motion_gap():
    tracker = StopTracker(still_seconds=10, stable_seconds=2, motion_max_age=5)
    tracker.arrived(stop(), 14)
    for t in range(15, 40):
        assert tracker.observe(scale_observation(None, t, state="unavailable"), None, t) is None
    assert tracker.observe(scale_observation(None, 40, state="unavailable"), motion("moving"), 40) == ("departure", stop(), None, True)


def test_a_heavier_wagon_replacing_the_standing_one_during_a_motion_outage_departs_the_old_stop():
    tracker = StopTracker(still_seconds=10, stable_seconds=2, rise_kg=5000)
    tracker.arrived(stop(), 14)
    assert tracker.observe(scale_observation(24120, 20), motion("still", 20), 20) is None
    for t in range(21, 27):
        tracker.observe(scale_observation(None, t, state="unavailable"), None, t)
    result = tracker.observe(scale_observation(61000, 27), motion("still", 40), 27)
    assert result == ("departure", stop(), 24120, True)
    assert tracker.standing is None
    tracker.observe(scale_observation(61000, 28), motion("still", 41), 28)
    assert tracker.observe(scale_observation(61000, 30), motion("still", 43), 30) == ("arrival", 61000.0)


def test_arrival_rejected_by_the_strict_read_restarts_the_stability_window():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    tracker.observe(scale_observation(62340, 12), motion("still", 11), 12)
    assert tracker.observe(scale_observation(62340, 14), motion("still", 13), 14) == ("arrival", 62340.0)
    tracker.arrival_rejected()
    assert tracker.observe(scale_observation(62340, 15), motion("still", 14), 15) is None
    assert tracker.observe(scale_observation(62340, 17), motion("still", 16), 17) == ("arrival", 62340.0)


def test_repeated_scale_reading_is_not_new_evidence():
    # The window opens at t=12; the repeated token at t=14 credits no time.
    # The fresh reading at t=15 follows fresh evidence 3s old, which is
    # within max(stable_seconds, motion_max_age)=5s, so the window survives
    # and 12->15 already satisfies stable_seconds=2.
    tracker = StopTracker(still_seconds=10, stable_seconds=2, motion_max_age=5)
    same = scale_observation(62340, 12)
    assert tracker.observe(same, motion("still", 11), 12) is None
    assert tracker.observe(same, motion("still", 13), 14) is None   # identical token: no time credited
    assert tracker.observe(scale_observation(62340, 15), motion("still", 14), 15) == ("arrival", 62340.0)


def test_a_two_second_token_refresh_under_a_one_second_poll_still_arrives():
    # Real hardware: the poll period is 1.0015s and the scale refreshes its
    # token every ~2s, so consecutive fresh readings are ~2.003s apart. With
    # the freshness-gap threshold at stable_seconds (2s) the window restarted
    # on every fresh reading and the wagon never arrived.
    tracker = StopTracker(still_seconds=10, stable_seconds=2, motion_max_age=5)
    now, token, current = 100.0, 0, scale_observation(62340, 0)
    arrival, elapsed = None, None
    for poll in range(12):
        if now - token >= 2.0:
            token = now
            current = scale_observation(62340, int(now * 1000))
        result = tracker.observe(current, motion("still", 10 + now - 100.0), now)
        if result is not None:
            arrival, elapsed = result, now - 100.0
            break
        now += 1.0015
    assert arrival == ("arrival", 62340.0)
    assert elapsed <= 4.5


def test_a_repeated_reading_does_not_starve_the_stability_window():
    # A 1 Hz poll against a scale that refreshes its token every ~2s must
    # still accumulate stable_seconds. A repeated token must give no time
    # credit but must NOT wipe the window, or arrival never happens.
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    fresh = scale_observation(62340, 20)
    assert tracker.observe(fresh, motion("still", 20), 20) is None   # window opens
    assert tracker.observe(fresh, motion("still", 21), 21) is None  # same token: no credit, window kept
    assert tracker.observe(scale_observation(62340, 22), motion("still", 22), 22) == ("arrival", 62340.0)


def test_a_still_counter_restart_clears_the_open_stability_window():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    assert tracker.observe(scale_observation(62340, 12), motion("still", 11), 12) is None   # window opens
    assert tracker.observe(scale_observation(62340, 13), motion("still", 0.5), 13) is None  # counter restarted: window clears
    assert tracker.observe(scale_observation(62340, 14), motion("still", 10), 14) is None   # window restarts
    assert tracker.observe(scale_observation(62340, 15), motion("still", 11), 15) is None
    assert tracker.observe(scale_observation(62340, 16), motion("still", 12), 16) == ("arrival", 62340.0)


def test_a_missing_sample_age_is_unknown_motion():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    assert tracker.observe(scale_observation(62340, 12), motion("still", 11, age=None), 12) is None
    assert tracker.motion_state == "unknown"


def test_a_stalled_poll_loop_flags_the_eventual_departure_as_a_motion_gap():
    tracker = StopTracker(still_seconds=10, stable_seconds=2, motion_max_age=5)
    tracker.arrived(stop(), 14)
    assert tracker.observe(scale_observation(24120, 15), motion("still", 15), 15) is None
    assert tracker.observe(scale_observation(24120, 30), motion("moving"), 30) == ("departure", stop(), 24120, True)


def stop_event(camera="cam8"):
    return {"version": 2, "kind": "wagon_stop", "id": "stop-1", "camera": camera, "weight_kg": 62340,
            "stable_weight_at": "2026-09-14T04:00:00+00:00", "scale_age_seconds": "0.1",
            "scale_updated_at": "t1", "still_seconds": 11.0}


@pytest.fixture
def armed(tmp_path, settings):
    """Коллектор, у которого под аркой уже стоит вагон stop-1 и ждёт кадр с номером."""
    settings.GO2RTC_API_URL = "http://video:1984"
    box = Outbox(tmp_path)
    event = stop_event()
    box.put(event)
    collector = WagonCollector(box)
    collector.tracker.arrived(event, 0.0)
    return SimpleNamespace(box=box, collector=collector, stop=event)


def test_wagon_collector_records_full_and_empty_weights_of_one_stop(tmp_path, settings):
    settings.WAGON_ARCH_STILL_SECONDS = 3
    settings.WAGON_ARCH_STABLE_SECONDS = 2
    baseline_threads = threading.active_count()
    box = Outbox(tmp_path)
    collector = WagonCollector(box)
    clock = {"now": 1000.0}
    scale_plan = iter(
        [(62340, "ready", True)] * 8 + [(50000, "unstable", False)] + [(24120, "ready", True)] * 2 + [(23900, "unstable", False)] * 2
    )
    motion_plan = iter([{"state": "moving", "still_seconds": 0.0, "sample_age_seconds": 0.2}] * 2
                       + [{"state": "still", "still_seconds": 3.0 + i, "sample_age_seconds": 0.2} for i in range(9)]
                       + [{"state": "moving", "still_seconds": 0.0, "sample_age_seconds": 0.2}] * 2)
    def observe(scale_key):
        assert scale_key == "wagon"
        weight, state, stable = next(scale_plan)
        return scale_observation(weight, clock["now"], stable=stable, state=state)
    strict = lambda scale_key: SimpleNamespace(weight_kg=Decimal(62340), age_seconds=Decimal("0.1"), updated_at="strict")
    with patch("weighbridge.wagon_collector.time", fake_time(clock)), \
            patch("apps.grain.scale.read_truck_scale_observation", side_effect=observe), \
            patch("apps.grain.scale.read_truck_scale", side_effect=strict), \
            patch("apps.cameras.ai.arch_motion", side_effect=lambda cam: next(motion_plan)), \
            patch.object(collector, "start_evidence"):
        for _ in range(13):
            collector.poll()
            clock["now"] += 1
    collector.close()
    assert threading.active_count() == baseline_threads  # evidence pool and outbox writer left no threads running
    db = sqlite3.connect(box.path)
    rows = [json.loads(body) for (body,) in db.execute("SELECT body FROM events ORDER BY seq")]
    assert [(row["kind"], row["weight_kg"]) for row in rows] == [("wagon_stop", 62340), ("wagon_departure", 24120)]
    assert rows[1]["stop_id"] == rows[0]["id"] and rows[1]["motion_gap"] is False
    assert db.execute("SELECT ready FROM events WHERE seq=2").fetchone()[0] == 1   # departure needs no evidence
    codes = [code for (code,) in db.execute("SELECT code FROM incidents ORDER BY id")]
    assert "wagon_arrived:62340" in codes and "wagon_departed:24120" in codes
    heartbeat = box.state("heartbeat")
    assert heartbeat["standing"] is None and heartbeat["status"] == "running"


@pytest.mark.parametrize(
    ("first_replies", "attempts"),
    [
        pytest.param(
            [
                wagon_reply(("2805553", False, False, None)),
                wagon_reply(("28055532", True, True, False)),   # confident, but the checksum fails
            ],
            3,
            id="rejected_numbers",
        ),
        # A malformed reply (None) must not crash evidence(); it is a failed
        # attempt and is retried, same as an AI outage.
        pytest.param([None], 2, id="malformed_reply"),
    ],
)
def test_wagon_collector_evidence_saves_the_frame_and_an_accepted_number_only(armed, first_replies, attempts):
    payloads = iter([*first_replies, wagon_reply(("28055531", True, True, True))])
    with patch("apps.grain.scale.open_local_request", side_effect=lambda request, timeout: BytesIO(JPEG)), \
            patch("apps.cameras.ai.detect_number", side_effect=lambda model, frame: next(payloads)) as detect, \
            patch.object(armed.collector, "_ocr_wait", lambda seconds: False):
        armed.collector.evidence(armed.stop)
    armed.collector.close()
    event = armed.box.next()
    assert event["photo"] == JPEG and event["photo_error"] == ""
    assert (event["number"], event["number_source"], event["ocr_attempts"]) == ("28055531", "model", attempts)
    assert detect.call_count == attempts


def test_wagon_collector_evidence_finishes_the_ocr_part_even_when_every_reply_is_malformed(armed, settings):
    settings.WAGON_ARCH_OCR_MAX_ATTEMPTS = 2
    # Every reply is unparseable garbage: evidence() must still finish the
    # "ocr" part (not hang the event at ready=0 forever) and stop at the cap.
    with patch("apps.grain.scale.open_local_request", side_effect=lambda request, timeout: BytesIO(JPEG)), \
            patch("apps.cameras.ai.detect_number", side_effect=lambda model, frame: None) as detect, \
            patch.object(armed.collector, "_ocr_wait", lambda seconds: False):
        armed.collector.evidence(armed.stop)
    armed.collector.close()
    event = armed.box.next()
    assert event is not None   # ready=1: the outbox is not head-of-line-blocked
    assert (event["number"], event["recognition_error"], event["ocr_attempts"]) == ("", "recognition_failed", 2)
    assert detect.call_count == 2


def test_wagon_collector_observation_gap_incident_fires_once_per_outage_not_every_poll(tmp_path, settings):
    box = Outbox(tmp_path)
    collector = WagonCollector(box)
    clock = {"now": 1000.0}
    calls = {"n": 0}

    def observe(scale_key):
        assert scale_key == "wagon"
        calls["n"] += 1
        if calls["n"] in (1, 10):
            return scale_observation(62340, calls["n"])
        raise OSError("scale link down")

    moving = {"state": "moving", "still_seconds": 0.0, "sample_age_seconds": 0.2}
    with patch("weighbridge.wagon_collector.time", fake_time(clock)), \
            patch("apps.grain.scale.read_truck_scale_observation", side_effect=observe), \
            patch("apps.cameras.ai.arch_motion", return_value=moving):
        for _ in range(10):   # 1 good reading, 8 polls unavailable, 1 good reading
            collector.poll()
            clock["now"] += 1
    collector.close()
    db = sqlite3.connect(box.path)
    codes = [code for (code,) in db.execute("SELECT code FROM incidents ORDER BY id")]
    assert codes.count("hardware_unavailable") == 1
    assert codes.count("observation_gap") == 1
    assert codes.count("running") == 2   # initial start, then the recovery
    assert codes[-1] == "running"


def test_wagon_collector_restores_the_standing_stop_after_a_restart(tmp_path, settings):
    box = Outbox(tmp_path)
    event = stop_event(settings.WAGON_ARCH_CAMERA)
    collector = WagonCollector(box)
    clock = {"now": 1000.0}
    still = {"state": "still", "still_seconds": 30.0, "sample_age_seconds": 0.2}
    with patch("weighbridge.wagon_collector.time", fake_time(clock)), \
            patch("apps.grain.scale.read_truck_scale_observation",
                  side_effect=lambda key: scale_observation(24120, clock["now"])), \
            patch("apps.cameras.ai.arch_motion", return_value=still):
        collector.tracker.arrived(event, clock["now"])
        collector.box.put(event)
        collector.remember_standing()
        collector.poll()       # one post-arrival stable reading persists last_stable
        clock["now"] += 1
    collector.close()

    restarted = WagonCollector(box)
    assert restarted.tracker.standing["id"] == "stop-1"
    assert restarted.tracker.motion_gap is True   # the restart interval was blind
    codes = [code for (code,) in sqlite3.connect(box.path).execute("SELECT code FROM incidents ORDER BY id")]
    assert "standing_restored:stop-1" in codes

    moving = {"state": "moving", "still_seconds": 0.0, "sample_age_seconds": 0.2}
    with patch("weighbridge.wagon_collector.time", fake_time(clock)), \
            patch("apps.grain.scale.read_truck_scale_observation",
                  side_effect=lambda key: scale_observation(23900, f"u{clock['now']}", state="unstable")), \
            patch("apps.cameras.ai.arch_motion", return_value=moving):
        restarted.poll()
    restarted.close()
    db = sqlite3.connect(box.path)
    rows = [json.loads(body) for (body,) in db.execute("SELECT body FROM events ORDER BY seq")]
    assert [row["kind"] for row in rows] == ["wagon_stop", "wagon_departure"]
    assert rows[1]["stop_id"] == "stop-1" and rows[1]["weight_kg"] == 24120
    assert rows[1]["motion_gap"] is True
    assert box.state("standing") == {}   # the arch is empty again


def test_wagon_collector_survives_a_strict_read_failure_and_arrives_on_the_next_one(tmp_path, settings):
    settings.WAGON_ARCH_STILL_SECONDS = 3
    settings.WAGON_ARCH_STABLE_SECONDS = 2
    box = Outbox(tmp_path)
    collector = WagonCollector(box)
    clock = {"now": 1000.0}
    strict_calls = {"n": 0}

    def strict(scale_key):
        strict_calls["n"] += 1
        if strict_calls["n"] == 1:
            raise scale.TruckScaleUnavailable("wagon scale link down")
        return SimpleNamespace(weight_kg=Decimal(62340), age_seconds=Decimal("0.1"), updated_at="strict")

    observations = {"n": 0}

    def observe(scale_key):
        observations["n"] += 1
        if observations["n"] == 1:
            raise OSError("wagon scale link down")   # the outage starts here
        return scale_observation(62340, clock["now"])

    still = {"state": "still", "still_seconds": 30.0, "sample_age_seconds": 0.2}
    with patch("weighbridge.wagon_collector.time", fake_time(clock)), \
            patch("apps.grain.scale.read_truck_scale_observation", side_effect=observe), \
            patch("apps.grain.scale.read_truck_scale", side_effect=strict), \
            patch("apps.cameras.ai.arch_motion", return_value=still), \
            patch.object(collector, "start_evidence"):
        for _ in range(8):
            collector.poll()
            clock["now"] += 1
    collector.close()
    assert strict_calls["n"] == 2   # the failure did not kill the poll loop
    db = sqlite3.connect(box.path)
    rows = [json.loads(body) for (body,) in db.execute("SELECT body FROM events ORDER BY seq")]
    assert [(row["kind"], row["weight_kg"]) for row in rows] == [("wagon_stop", 62340)]
    codes = [code for (code,) in db.execute("SELECT code FROM incidents ORDER BY id")]
    # The failed strict read earns the poll no scale-link credit, so it counts
    # towards the same outage window as the failed observation before it.
    assert "hardware_unavailable" in codes


def test_wagon_collector_evidence_retries_a_frame_fetch_that_raises(armed, settings):
    settings.WAGON_ARCH_OCR_MAX_ATTEMPTS = 4
    fetches = {"n": 0}

    def open_request(request, timeout):
        # A cold cam8main stream: urllib raises http.client errors, which are
        # neither OSError nor ValueError, until the relay has a frame.
        fetches["n"] += 1
        if fetches["n"] <= 3:
            raise http.client.IncompleteRead(b"")
        return BytesIO(JPEG)

    with patch("apps.grain.scale.open_local_request", side_effect=open_request), \
            patch("apps.cameras.ai.detect_number",
                  return_value=wagon_reply(("28055531", True, True, True))) as detect, \
            patch.object(armed.collector, "_ocr_wait", lambda seconds: False):
        armed.collector.evidence(armed.stop)
    armed.collector.close()
    event = armed.box.next()
    assert event is not None                          # ready=1: both parts finished
    assert event["photo"] == JPEG and event["photo_error"] == ""
    assert event["number"] == "28055531"
    assert detect.call_count == 1                     # OCR only runs once a frame exists
    assert fetches["n"] == 4


def test_wagon_collector_evidence_finishes_both_parts_when_no_frame_ever_arrives(armed, settings):
    settings.WAGON_ARCH_OCR_MAX_ATTEMPTS = 3
    fetches = {"n": 0}

    def open_request(request, timeout):
        fetches["n"] += 1
        raise http.client.IncompleteRead(b"")

    with patch("apps.grain.scale.open_local_request", side_effect=open_request), \
            patch("apps.cameras.ai.detect_number", side_effect=AssertionError("no frame to OCR")), \
            patch.object(armed.collector, "_ocr_wait", lambda seconds: False):
        armed.collector.evidence(armed.stop)
    armed.collector.close()
    event = armed.box.next()
    assert event is not None                                  # ready=1 despite a dead camera
    assert event["photo"] is None and event["photo_error"] == "snapshot_unavailable"
    assert (event["number"], event["ocr_attempts"]) == ("", 0)
    assert event["recognition_error"] == "recognition_not_started"
    assert fetches["n"] == 3                                  # bounded by the attempt budget


def test_wagon_collector_evidence_does_not_wait_after_the_last_attempt(armed, settings):
    settings.WAGON_ARCH_OCR_MAX_ATTEMPTS = 3
    waits = []

    def ocr_wait(seconds):
        waits.append(seconds)
        return False

    # The last cycle has no frame either: nothing is left to retry, so the
    # row must be finished at once, not one more retry pause later.
    with patch("apps.grain.scale.open_local_request", side_effect=http.client.IncompleteRead(b"")), \
            patch.object(armed.collector, "_ocr_wait", ocr_wait):
        armed.collector.evidence(armed.stop)
    armed.collector.close()
    assert armed.box.next() is not None
    assert len(waits) == 2   # between the three attempts, none after the last


def test_wagon_collector_departure_is_stamped_with_the_exit_reading_time(tmp_path, settings):
    box = Outbox(tmp_path)
    clock = {"now": 1000.0}
    fake_time = SimpleNamespace(monotonic=lambda: clock["now"], time=lambda: 1_800_000_000 + clock["now"],
                                sleep=real_time.sleep)
    event = stop_event(settings.WAGON_ARCH_CAMERA)
    still = {"state": "still", "still_seconds": 30.0, "sample_age_seconds": 0.2}
    moving = {"state": "moving", "still_seconds": 0.0, "sample_age_seconds": 0.2}
    with patch("weighbridge.wagon_collector.time", fake_time), \
            patch("apps.grain.scale.read_truck_scale_observation",
                  side_effect=lambda key: scale_observation(24120, "-exit")), \
            patch("apps.cameras.ai.arch_motion", return_value=still):
        collector = WagonCollector(box)
        collector.tracker.arrived(event, clock["now"])
        collector.box.put(event)
        collector.poll()   # the emptied wagon settles: this is the exit weight
        for _ in range(4):   # the same reading again: no new stable evidence
            clock["now"] += 1
            collector.poll()
        collector.close()
        # The collector restarts while the empty wagon keeps standing.
        clock["now"] += 100
        restarted = WagonCollector(box)
        clock["now"] += 200
    with patch("weighbridge.wagon_collector.time", fake_time), \
            patch("apps.grain.scale.read_truck_scale_observation",
                  side_effect=lambda key: scale_observation(23900, "-leave", state="unstable")), \
            patch("apps.cameras.ai.arch_motion", return_value=moving):
        restarted.poll()
    restarted.close()
    db = sqlite3.connect(box.path)
    departure = [json.loads(body) for (body,) in db.execute("SELECT body FROM events ORDER BY seq")][-1]
    assert departure["kind"] == "wagon_departure" and departure["weight_kg"] == 24120
    # The exit weight was read 304 s before the wagon left, across a restart.
    gap = datetime.fromisoformat(departure["departed_at"]) - datetime.fromisoformat(departure["stable_weight_at"])
    assert abs(gap.total_seconds() - 304) < 5
    assert "scale_updated_at" not in departure   # the importer never reads it
