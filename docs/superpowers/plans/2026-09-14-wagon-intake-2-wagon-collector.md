# Wagon Intake · Part 2 — Wagon-scale collector

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone collector process records every wagon stop under the unloading arch — full weight on arrival (with a cam8 frame and the wagon number), empty weight before departure — into its own crash-safe SQLite outbox, using the wagon scale (`WAGON_SCALE_API_URL`) and the camera-PC motion state from Part 1.

**Architecture:** Mirror the truck collector (`backend/weighbridge/collector.py`): a 1-second poll loop, a pure state machine (`StopTracker`, like `Lane`), the same `Outbox` class in a second volume, evidence workers for the frame and OCR, heartbeat + incidents. Two outbox rows per stop: `wagon_stop` (arrival; photo + OCR parts) and `wagon_departure` (no evidence; both parts finished at once) — the outbox stays append-only and FIFO. The CRM importer (Part 3) turns them into intake trips.

**Tech Stack:** Python 3.12 (Django settings only, no DB), SQLite WAL outbox, `urllib`, Docker Compose project `asyl-weighbridge`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-wagon-intake-arch-design.md` (section «2. Сборщик вагонных весов»).

**Repository:** `/Users/dimash/PycharmProjects/asyl-ltd`. Backend tests: `cd backend && .venv/bin/pytest <path> -q -p no:cacheprovider`.

## Global Constraints

- The truck contour is untouched: its container, volume (`asyl-weighbridge-outbox`) and `enabled` marker stay as they are. The wagon collector has its own volume `asyl-weighbridge-wagon-outbox` mounted at `/var/lib/weighbridge-wagon` and no activation marker (nothing legacy to replace; the CRM flag `WAGON_ARCH_AUTOMATION_ENABLED` gates the importer in Part 3).
- Settings (Django `base.py`, read by `config.weighbridge_settings`): `WAGON_ARCH_CAMERA` (default `cam8`, `cam1..cam32`), `WAGON_ARCH_STILL_SECONDS` (10, 3–120), `WAGON_ARCH_STABLE_SECONDS` (2, 1–30), `WAGON_ARCH_STABLE_TOLERANCE_KG` (100, 0–2000), `WAGON_ARCH_EMPTY_MAX_KG` (1000, 0–20000), `WAGON_ARCH_NEXT_WAGON_RISE_KG` (5000, 500–50000), `WAGON_ARCH_MOTION_MAX_AGE_SECONDS` (5, 1–60), `WAGON_ARCH_OCR_RETRY_SECONDS` (15, 5–300), `WAGON_ARCH_OCR_MAX_ATTEMPTS` (4, 1–10), `WAGON_ARCH_AUTOMATION_ENABLED` (flag, default `0`, used in Part 3).
- Scale reads reuse `apps.grain.scale.read_truck_scale_observation("wagon")` / `read_truck_scale("wagon")` (they take `scale_key`; the shared `TRUCK_SCALE_*` timeout/age settings apply).
- Motion comes from `GET {AI_SERVICE_URL}/cameras/<cam>/arch-motion` (Part 1 contract): `state ∈ {moving, still, unknown}`, `still_seconds`, `sample_age_seconds`. Anything else (error, stale sample, `unknown`) is "unknown" and never causes a transition.
- Wagon numbers use the existing `apps.cameras.ai.detect_wagon_plate(frame_bytes)` + `accepted_plate_number(payload)`; a number is stored only when OCR itself marked it `accepted`.
- Event bodies (JSON in `events.body`):

```python
# arrival — photo/ocr parts finished by evidence workers
{"version": 2, "kind": "wagon_stop", "id": "<uuid>", "camera": "cam8",
 "weight_kg": 62340, "stable_weight_at": "<iso>", "scale_age_seconds": "0.1",
 "scale_updated_at": "<scale ts>", "still_seconds": 11.0,
 # added by finish(): "photo" blob, "photo_error", "number", "number_source": "model"|"",
 # "recognition": {...trimmed OCR payload...}, "recognition_error", "ocr_attempts"}
# departure — both parts finished immediately
{"version": 2, "kind": "wagon_departure", "id": "<uuid>", "stop_id": "<arrival uuid>",
 "camera": "cam8", "weight_kg": 24120 | None, "stable_weight_at": "<iso>" | None,
 "scale_updated_at": "<scale ts>" | None, "departed_at": "<iso>", "motion_gap": false}
```

---

## File structure

| File | Responsibility |
|---|---|
| `backend/weighbridge/writer.py` | `OutboxWriter` — the single-thread FIFO write queue extracted from `Collector` (shared by both collectors) |
| `backend/weighbridge/collector.py` | truck collector, now delegating to `OutboxWriter` (behaviour unchanged) |
| `backend/weighbridge/wagon_stop.py` | `StopTracker` — pure arrival/departure state machine |
| `backend/weighbridge/wagon_collector.py` | `WagonCollector` process: poll loop, evidence, heartbeat, `main()` |
| `backend/apps/cameras/ai.py` | `arch_motion(cam)` client helper |
| `backend/config/_settings/base.py` | `WAGON_ARCH_*` settings |
| `deploy/weighbridge/compose.yml`, `go2rtc.yaml`, `install.sh`, `README.md` | second collector service, `cam8main` relay stream, volume ownership |
| `docker-compose.prod.yml`, `docker-compose.yml`, `.env.example` | second outbox volume mounted into the monitor/backend; env documentation |
| `backend/apps/grain/tests/test_wagon_collector.py` | tests for tracker + collector |

---

### Task 1: Extract `OutboxWriter` from the truck collector (no behaviour change)

**Files:**
- Create: `backend/weighbridge/writer.py`
- Modify: `backend/weighbridge/collector.py:41-95` (`writer`, `pending_writes`, `write_lock`, `writer_future`, `enqueue_write`, `start_writer`, `flush_writes`, `close`)
- Test: `backend/apps/grain/tests/test_durable_outbox.py` (existing collector tests must stay green) + one new writer test

**Interfaces:**
- Produces: `OutboxWriter(box)` with `enqueue(method: str, *args, **kwargs) -> None`, `start() -> None`, `pending() -> int`, `drain(timeout: float = 10.0) -> bool` (returns `False` when writes are still pending after the deadline). `Collector` keeps `enqueue_write(...)`, `start_writer()` and `pending_writes` **as thin aliases** (`self.pending_writes` is read by `_poll` for the heartbeat and by `close`), so existing tests need no change.

- [ ] **Step 1: Write the failing writer test**

Append to `backend/apps/grain/tests/test_durable_outbox.py`:

```python
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
    first, second = event(), event()
    writer.enqueue("put", first)
    writer.enqueue("put", second)
    writer.start()
    assert writer.drain(timeout=5.0) is False or writer.pending() == 0
    writer.start()
    assert writer.drain(timeout=5.0) is True
    assert calls == [first["id"], first["id"], second["id"]]
    assert box.counts()["total"] == 2
    writer.shutdown()
```

Add `import sqlite3` to the test module imports if it is not there yet.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_durable_outbox.py -q -p no:cacheprovider -k outbox_writer`
Expected: FAIL with `ModuleNotFoundError: No module named 'weighbridge.writer'`.

- [ ] **Step 3: Create the writer and delegate from `Collector`**

Create `backend/weighbridge/writer.py`:

```python
"""Single-thread FIFO writer: outbox rows are committed in capture order.

Contention delays persistence; it never reorders, drops or replaces a value.
"""
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .outbox import is_busy


class OutboxWriter:
    def __init__(self, box):
        self.box = box
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="outbox")
        self.pending_writes = deque()
        self.lock = threading.Lock()
        self.future = None

    def enqueue(self, method, *args, **kwargs):
        with self.lock:
            self.pending_writes.append((method, args, kwargs))
        self.start()

    def pending(self):
        return len(self.pending_writes)

    def start(self):
        with self.lock:
            if self.future is not None:
                if not self.future.done():
                    return
                self.future.result()
            if self.pending_writes:
                self.future = self.executor.submit(self.flush)

    def flush(self):
        while True:
            with self.lock:
                if not self.pending_writes:
                    return
                method, args, kwargs = self.pending_writes[0]
            try:
                getattr(self.box, method)(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not is_busy(exc):
                    raise
                return  # retained FIFO; the next start() retries off-thread
            with self.lock:
                self.pending_writes.popleft()

    def drain(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while self.pending_writes and time.monotonic() < deadline:
            self.start()
            if self.future is not None:
                self.future.result()
            if self.pending_writes:
                time.sleep(.05)
        return not self.pending_writes

    def shutdown(self):
        self.executor.shutdown(wait=True)
```

In `backend/weighbridge/collector.py`: replace the writer fields in `__init__` (`self.writer = ThreadPoolExecutor(...)`, `self.pending_writes`, `self.write_lock`, `self.writer_future`) with `self.writer = OutboxWriter(box)`; delete `enqueue_write`, `start_writer`, `flush_writes` bodies and replace with:

```python
    @property
    def pending_writes(self):
        return self.writer.pending_writes

    def enqueue_write(self, method, *args, **kwargs):
        # The event and evidence are immutable values owned by this capture.
        # Contention may delay persistence, never cause a replacement reading
        # or a delayed live camera request.
        self.writer.enqueue(method, *args, **kwargs)

    def start_writer(self):
        self.writer.start()
```

and `close()` becomes:

```python
    def close(self):
        self.pool.shutdown(wait=True)
        # Each flush is bounded by SQLite's busy timeout; there is no infinite
        # worker retry loop holding process shutdown. Normal shutdown drains all
        # captured evidence before the writer exits.
        drained = self.writer.drain(timeout=10)
        self.writer.shutdown()
        if not drained:
            print("outbox_storage_unavailable_unflushed_evidence", flush=True)
```

Remove the now-unused `deque`/`threading`/`ThreadPoolExecutor` imports from `collector.py` only if nothing else uses them (`pool = ThreadPoolExecutor(...)` still does; `threading` is used by `main()`).

- [ ] **Step 4: Run the outbox suite**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_durable_outbox.py apps/grain/tests/test_collector_photo_recovery.py -q -p no:cacheprovider`
Expected: PASS (26 + existing photo-recovery tests).

- [ ] **Step 5: Commit**

```bash
git add backend/weighbridge/writer.py backend/weighbridge/collector.py backend/apps/grain/tests/test_durable_outbox.py
git commit -m "refactor(weighbridge): extract the FIFO outbox writer for reuse"
```

---

### Task 2: `StopTracker` state machine

**Files:**
- Create: `backend/weighbridge/wagon_stop.py`
- Test: `backend/apps/grain/tests/test_wagon_collector.py` (new)

**Interfaces:**
- Produces: `StopTracker(*, still_seconds=10.0, stable_seconds=2.0, tolerance=100, empty_max=1000, rise_kg=5000, motion_max_age=5.0)`; `observe(observation: ScaleObservation, motion: Mapping | None, now: float) -> tuple | None` returning `("arrival", weight_kg)`, `("departure", stop: dict, exit_weight_kg: int | None, motion_gap: bool)` or `None`; `arrived(stop: dict, now: float) -> None` (called after the strict read succeeded); `arrival_rejected() -> None`; attributes `standing` (current stop dict or `None`), `motion_state`, `last_stable` (`(weight_kg, scale_updated_at, observed_at)` or `None`).

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/grain/tests/test_wagon_collector.py`:

```python
from decimal import Decimal

from apps.grain.scale import ScaleObservation
from weighbridge.wagon_stop import StopTracker


def observation(weight, second, *, stable=True, state="ready"):
    return ScaleObservation(state, Decimal(weight) if weight is not None else None, True, stable and state == "ready", False, Decimal("0.1"), f"t{second}")


def motion(state, still_seconds=0.0, age=0.3):
    return {"state": state, "still_seconds": still_seconds, "sample_age_seconds": age}


def stop(weight=62340):
    return {"id": "stop-1", "weight_kg": weight, "scale_updated_at": "t5"}


def test_arrival_needs_ten_still_seconds_and_a_stable_full_weight():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    assert tracker.observe(observation(62340, 1), motion("moving"), 1) is None
    for t in range(2, 6):  # still, but not yet for ten seconds
        assert tracker.observe(observation(62340, t), motion("still", t - 1), t) is None
    assert tracker.observe(observation(62340, 12), motion("still", 11), 12) is None   # stability window starts
    assert tracker.observe(observation(62340, 13), motion("still", 12), 13) is None
    assert tracker.observe(observation(62340, 14), motion("still", 13), 14) == ("arrival", 62340.0)


def test_unstable_or_light_weight_and_unknown_motion_do_not_arrive():
    tracker = StopTracker(still_seconds=10, stable_seconds=2, empty_max=1000)
    for t in range(12, 20):
        assert tracker.observe(observation(62340, t, stable=False), motion("still", 20), t) is None
    for t in range(20, 25):
        assert tracker.observe(observation(800, t), motion("still", 30), t) is None
    for t in range(25, 30):
        assert tracker.observe(observation(62340, t), None, t) is None
    for t in range(30, 35):
        assert tracker.observe(observation(62340, t), motion("still", 40, age=9.0), t) is None
    assert tracker.observe(observation(62340, 35), motion("still", 45), 35) is None
    assert tracker.observe(observation(62340, 37), motion("still", 47), 37) == ("arrival", 62340.0)


def test_departure_reports_the_last_stable_weight_before_motion():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    tracker.arrived(stop(), 14)
    assert tracker.standing["id"] == "stop-1"
    for t, weight, stable in ((20, 50000, True), (21, 40200, False), (22, 24120, True), (23, 24080, False)):
        assert tracker.observe(observation(weight, t, stable=stable), motion("still", t), t) is None
    result = tracker.observe(observation(23990, 24, stable=False), motion("moving"), 24)
    assert result == ("departure", stop(), 24120, False)
    assert tracker.standing is None and tracker.last_stable is None


def test_departure_without_any_stable_weight_reports_none_and_flags_a_motion_gap():
    tracker = StopTracker(still_seconds=10, stable_seconds=2, motion_max_age=5)
    tracker.arrived(stop(), 14)
    for t in range(15, 40):
        assert tracker.observe(observation(None, t, state="unavailable"), None, t) is None
    assert tracker.observe(observation(None, 40, state="unavailable"), motion("moving"), 40) == ("departure", stop(), None, True)


def test_a_heavier_wagon_replacing_the_standing_one_during_a_motion_outage_departs_the_old_stop():
    tracker = StopTracker(still_seconds=10, stable_seconds=2, rise_kg=5000)
    tracker.arrived(stop(), 14)
    assert tracker.observe(observation(24120, 20), motion("still", 20), 20) is None
    for t in range(21, 27):
        tracker.observe(observation(None, t, state="unavailable"), None, t)
    result = tracker.observe(observation(61000, 27), motion("still", 40), 27)
    assert result == ("departure", stop(), 24120, True)
    assert tracker.standing is None
    tracker.observe(observation(61000, 28), motion("still", 41), 28)
    assert tracker.observe(observation(61000, 30), motion("still", 43), 30) == ("arrival", 61000.0)


def test_arrival_rejected_by_the_strict_read_restarts_the_stability_window():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    tracker.observe(observation(62340, 12), motion("still", 11), 12)
    assert tracker.observe(observation(62340, 14), motion("still", 13), 14) == ("arrival", 62340.0)
    tracker.arrival_rejected()
    assert tracker.observe(observation(62340, 15), motion("still", 14), 15) is None
    assert tracker.observe(observation(62340, 17), motion("still", 16), 17) == ("arrival", 62340.0)


def test_repeated_scale_reading_is_not_new_evidence():
    tracker = StopTracker(still_seconds=10, stable_seconds=2)
    same = observation(62340, 12)
    assert tracker.observe(same, motion("still", 11), 12) is None
    assert tracker.observe(same, motion("still", 13), 14) is None   # identical token: no time credited
    assert tracker.observe(observation(62340, 15), motion("still", 14), 15) is None
    assert tracker.observe(observation(62340, 17), motion("still", 16), 17) == ("arrival", 62340.0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_collector.py -q -p no:cacheprovider`
Expected: FAIL at import: `ModuleNotFoundError: No module named 'weighbridge.wagon_stop'`.

- [ ] **Step 3: Implement the tracker**

Create `backend/weighbridge/wagon_stop.py`:

```python
"""One wagon stop under the unloading arch, decided from motion and weight.

Arrival: the arch zone has been still for ``still_seconds`` and the wagon
scale shows a stable full weight for ``stable_seconds``. Departure: the zone
moves again; the exit weight is the last stable reading seen while standing.
Unknown motion (camera PC silent or stale) never moves the machine.
"""

VALID_SCALE_STATES = {"ready", "unstable"}


class StopTracker:
    def __init__(self, *, still_seconds=10.0, stable_seconds=2.0, tolerance=100,
                 empty_max=1000, rise_kg=5000, motion_max_age=5.0):
        self.still_seconds, self.stable_seconds = float(still_seconds), float(stable_seconds)
        self.tolerance, self.empty_max, self.rise_kg = tolerance, empty_max, rise_kg
        self.motion_max_age = float(motion_max_age)
        self.standing = None
        self.last_stable = None
        self.since = self.weight = None
        self.last_token = None
        self.motion_state = "unknown"
        self.motion_gap = False

    def _motion_state(self, motion):
        if not isinstance(motion, dict) or motion.get("state") not in {"moving", "still"}:
            return "unknown"
        try:
            age = float(motion.get("sample_age_seconds") or 0.0)
            still_seconds = float(motion.get("still_seconds") or 0.0)
        except (TypeError, ValueError):
            return "unknown"
        if age > self.motion_max_age:
            return "unknown"
        self._still_seconds = still_seconds
        return motion["state"]

    def observe(self, observation, motion, now):
        self._still_seconds = 0.0
        state = self._motion_state(motion)
        self.motion_state = state
        valid = observation.state in VALID_SCALE_STATES and observation.weight_kg is not None
        token = observation.updated_at if valid else None
        fresh = bool(valid and token and token != self.last_token)
        if fresh:
            self.last_token = token
        weight = float(observation.weight_kg) if valid else None
        if self.standing is not None:
            if state == "unknown":
                self.motion_gap = True
            if fresh and observation.stable and weight > self.empty_max:
                if weight > float(self.standing["weight_kg"]) + self.rise_kg or (
                    self.last_stable is not None and weight > self.last_stable[0] + self.rise_kg
                ):
                    # A heavier wagon stands here: the previous one left unseen.
                    return self._depart(gap=True)
                self.last_stable = (int(round(weight)), observation.updated_at, now)
            if state == "moving":
                return self._depart(gap=self.motion_gap)
            return None
        if state != "still":
            self.since = self.weight = None
            return None
        if self._still_seconds < self.still_seconds or not fresh:
            return None
        if not observation.stable or weight <= self.empty_max:
            self.since = self.weight = None
            return None
        if self.since is None or abs(weight - self.weight) > self.tolerance:
            self.since, self.weight = now, weight
            return None
        if now - self.since >= self.stable_seconds:
            return ("arrival", weight)
        return None

    def _depart(self, *, gap):
        stop, exit_weight = self.standing, self.last_stable[0] if self.last_stable else None
        self.standing = self.last_stable = None
        self.since = self.weight = None
        self.motion_gap = False
        return ("departure", stop, exit_weight, bool(gap))

    def arrived(self, stop, now):
        self.standing = stop
        self.last_stable = (int(stop["weight_kg"]), stop.get("scale_updated_at"), now)
        self.since = self.weight = None
        self.motion_gap = False

    def arrival_rejected(self):
        self.since = self.weight = None
```

Check the tests against this behaviour: in `test_a_heavier_wagon…` the departure carries `motion_gap=True` because motion was unknown while standing; in `test_departure_reports_the_last_stable…` motion was always known → `False`.

- [ ] **Step 4: Run the tracker tests**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_collector.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/weighbridge/wagon_stop.py backend/apps/grain/tests/test_wagon_collector.py
git commit -m "feat(weighbridge): wagon stop tracker (arrival on stillness, departure on motion)"
```

---

### Task 3: `ai.arch_motion(cam)` client and `WAGON_ARCH_*` settings

**Files:**
- Modify: `backend/apps/cameras/ai.py` (after `save_vehicle_roi` ~line 333)
- Modify: `backend/config/_settings/base.py` (after `VEHICLE_PLATE_AUTO_SCALE_REARM_DELTA_KG` ~line 531)
- Test: `backend/apps/cameras/tests/test_ai.py` (append), `backend/config/tests/test_vehicle_weight_first_settings.py` (append)

**Interfaces:**
- Produces: `ai.arch_motion(cam: str) -> dict` (`GET /cameras/<cam>/arch-motion`, `VEHICLE_RUNTIME_PROBE_TIMEOUT`, raises `AiError`/`AiUnavailable`), `ai.arch_zone(cam) -> dict`, `ai.save_arch_zone(cam, payload) -> tuple[int, dict]` (used by Part 4); settings listed in Global Constraints.

- [ ] **Step 1: Write the failing tests**

Append to `backend/apps/cameras/tests/test_ai.py` (follow the file's existing patching style; the module-level `_call`/`_request` are patchable):

```python
def test_arch_motion_and_zone_helpers_address_the_camera_pc_arch_endpoints():
    from unittest.mock import patch
    from apps.cameras import ai
    with patch.object(ai, "_call", return_value={"state": "still", "still_seconds": 12.0}) as call:
        assert ai.arch_motion("8") == {"state": "still", "still_seconds": 12.0}
        assert ai.arch_zone("cam8") == {"state": "still", "still_seconds": 12.0}
    assert call.call_args_list[0].args == ("GET", "/cameras/cam8/arch-motion")
    assert call.call_args_list[0].kwargs == {"timeout_seconds": ai.VEHICLE_RUNTIME_PROBE_TIMEOUT}
    assert call.call_args_list[1].args == ("GET", "/cameras/cam8/arch-zone")
    with patch.object(ai, "_request", return_value=(200, {"ok": True})) as request:
        assert ai.save_arch_zone("cam8", {"points": []}) == (200, {"ok": True})
    assert request.call_args.args == ("PUT", "/cameras/cam8/arch-zone", {"points": []})
```

Append to `backend/config/tests/test_vehicle_weight_first_settings.py` (mirror its existing importlib-reload style for env-driven settings; if the file exposes a helper that reloads `config._settings.base` with a given environment, use it):

```python
def test_wagon_arch_settings_have_safe_defaults_and_bounds(reload_base_settings):
    base = reload_base_settings({})
    assert base.WAGON_ARCH_AUTOMATION_ENABLED is False
    assert base.WAGON_ARCH_CAMERA == "cam8"
    assert (base.WAGON_ARCH_STILL_SECONDS, base.WAGON_ARCH_STABLE_SECONDS) == (10, 2)
    assert (base.WAGON_ARCH_STABLE_TOLERANCE_KG, base.WAGON_ARCH_EMPTY_MAX_KG, base.WAGON_ARCH_NEXT_WAGON_RISE_KG) == (100, 1000, 5000)
    assert (base.WAGON_ARCH_MOTION_MAX_AGE_SECONDS, base.WAGON_ARCH_OCR_RETRY_SECONDS, base.WAGON_ARCH_OCR_MAX_ATTEMPTS) == (5, 15, 4)
    with pytest.raises(ValueError):
        reload_base_settings({"WAGON_ARCH_STILL_SECONDS": "1"})
    with pytest.raises(ValueError):
        reload_base_settings({"WAGON_ARCH_CAMERA": "front"})
```

If that test module has no such fixture, define `reload_base_settings` at its top the way the module's other tests reload settings (search the file for `importlib.reload` / `monkeypatch.setenv` and copy that mechanism into a fixture returning the reloaded module).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/pytest apps/cameras/tests/test_ai.py config/tests/test_vehicle_weight_first_settings.py -q -p no:cacheprovider -k "arch"`
Expected: FAIL with `AttributeError: module 'apps.cameras.ai' has no attribute 'arch_motion'` and `AttributeError: ... WAGON_ARCH_AUTOMATION_ENABLED`.

- [ ] **Step 3: Implement**

In `backend/apps/cameras/ai.py`, after `save_vehicle_roi`:

```python
def arch_motion(cam: str) -> dict:
    """Состояние зоны арки вагонных весов: едет / стоит и сколько секунд стоит."""
    return _call(
        "GET",
        f"/cameras/{camera_id(cam)}/arch-motion",
        timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT,
    ) or {}


def arch_zone(cam: str) -> dict:
    return _call(
        "GET",
        f"/cameras/{camera_id(cam)}/arch-zone",
        timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT,
    ) or {}


def save_arch_zone(cam: str, payload: dict) -> tuple[int, dict]:
    return _request(
        "PUT",
        f"/cameras/{camera_id(cam)}/arch-zone",
        payload,
        timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT,
    )
```

(`camera_id("8")` — check the existing `camera_id` accepts only `cam<N>`; if it rejects bare digits, the test's `ai.arch_motion("8")` must become `"cam8"`. Keep the semantics of `vehicle_roi` exactly.)

In `backend/config/_settings/base.py`, after `VEHICLE_PLATE_AUTO_SCALE_REARM_DELTA_KG`:

```python
# Wagon intake under the unloading arch: an independent collector pairs the
# wagon scale with the camera-PC arch-motion state (see weighbridge/wagon_collector).
WAGON_ARCH_AUTOMATION_ENABLED = env_flag("WAGON_ARCH_AUTOMATION_ENABLED", "0")
WAGON_ARCH_CAMERA = os.environ.get("WAGON_ARCH_CAMERA", "cam8").strip().lower()
if not re.fullmatch(r"cam(?:[1-9]|[12][0-9]|3[0-2])", WAGON_ARCH_CAMERA):
    raise ValueError("WAGON_ARCH_CAMERA must be cam1..cam32")
WAGON_ARCH_STILL_SECONDS = _bounded_int_env("WAGON_ARCH_STILL_SECONDS", 10, 3, 120)
WAGON_ARCH_STABLE_SECONDS = _bounded_int_env("WAGON_ARCH_STABLE_SECONDS", 2, 1, 30)
WAGON_ARCH_STABLE_TOLERANCE_KG = _bounded_int_env("WAGON_ARCH_STABLE_TOLERANCE_KG", 100, 0, 2_000)
WAGON_ARCH_EMPTY_MAX_KG = _bounded_int_env("WAGON_ARCH_EMPTY_MAX_KG", 1000, 0, 20_000)
WAGON_ARCH_NEXT_WAGON_RISE_KG = _bounded_int_env("WAGON_ARCH_NEXT_WAGON_RISE_KG", 5000, 500, 50_000)
WAGON_ARCH_MOTION_MAX_AGE_SECONDS = _bounded_int_env("WAGON_ARCH_MOTION_MAX_AGE_SECONDS", 5, 1, 60)
WAGON_ARCH_OCR_RETRY_SECONDS = _bounded_int_env("WAGON_ARCH_OCR_RETRY_SECONDS", 15, 5, 300)
WAGON_ARCH_OCR_MAX_ATTEMPTS = _bounded_int_env("WAGON_ARCH_OCR_MAX_ATTEMPTS", 4, 1, 10)
```

(`env_flag` and `re` are already used in this module for `VEHICLE_PLATE_AUTO_SCALE_ENABLED` / `VEHICLE_PLATE_WEIGHT_FIRST_CAMERA`; reuse the same helper names as found at lines ~481–495.)

- [ ] **Step 4: Run the tests**

Run: `cd backend && .venv/bin/pytest apps/cameras/tests/test_ai.py config/tests -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/cameras/ai.py backend/config/_settings/base.py backend/apps/cameras/tests/test_ai.py backend/config/tests/test_vehicle_weight_first_settings.py
git commit -m "feat(cameras): arch-motion/arch-zone client helpers and WAGON_ARCH_* settings"
```

---

### Task 4: `WagonCollector` process

**Files:**
- Create: `backend/weighbridge/wagon_collector.py`
- Test: `backend/apps/grain/tests/test_wagon_collector.py` (append)

**Interfaces:**
- Consumes: `Outbox` (`put/finish/state/incident/recover`), `OutboxWriter` (Task 1), `StopTracker` (Task 2), `scale.read_truck_scale_observation("wagon")`, `scale.read_truck_scale("wagon")`, `scale._open_request`, `ai.arch_motion`, `ai.detect_wagon_plate`, `ai.accepted_plate_number`, settings from Task 3, `settings.GO2RTC_API_URL`.
- Produces: `python -m weighbridge.wagon_collector` writing the event bodies from Global Constraints; heartbeat `{"updated_at", "status", "standing", "motion", "pending_writes"}`; incidents `collector_started`, `running`, `hardware_unavailable`, `camera_unavailable`, `observation_gap`, `wagon_arrived:<kg>`, `wagon_departed:<kg|none>`, `wagon_departed_unseen:<kg|none>`.

- [ ] **Step 1: Write the failing collector test**

Append to `backend/apps/grain/tests/test_wagon_collector.py`:

```python
import json
import sqlite3
import time as real_time
from types import SimpleNamespace
from unittest.mock import patch

from weighbridge.outbox import Outbox


def test_wagon_collector_records_full_and_empty_weights_of_one_stop(tmp_path, settings):
    from weighbridge.wagon_collector import WagonCollector
    settings.WAGON_ARCH_STILL_SECONDS = 3
    settings.WAGON_ARCH_STABLE_SECONDS = 2
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
        return ScaleObservation(state, Decimal(weight), True, stable, False, Decimal("0.1"), f"t{clock['now']}")
    strict = lambda scale_key: SimpleNamespace(weight_kg=Decimal(62340), age_seconds=Decimal("0.1"), updated_at="strict")
    fake_time = SimpleNamespace(monotonic=lambda: clock["now"], time=real_time.time, sleep=real_time.sleep)
    with patch("weighbridge.wagon_collector.time", fake_time), \
            patch("apps.grain.scale.read_truck_scale_observation", side_effect=observe), \
            patch("apps.grain.scale.read_truck_scale", side_effect=strict), \
            patch("apps.cameras.ai.arch_motion", side_effect=lambda cam: next(motion_plan)), \
            patch.object(collector, "start_evidence"):
        for _ in range(13):
            collector.poll()
            clock["now"] += 1
    collector.close()
    db = sqlite3.connect(box.path)
    rows = [json.loads(body) for (body,) in db.execute("SELECT body FROM events ORDER BY seq")]
    assert [(row["kind"], row["weight_kg"]) for row in rows] == [("wagon_stop", 62340), ("wagon_departure", 24120)]
    assert rows[1]["stop_id"] == rows[0]["id"] and rows[1]["motion_gap"] is False
    assert db.execute("SELECT ready FROM events WHERE seq=2").fetchone()[0] == 1   # departure needs no evidence
    codes = [code for (code,) in db.execute("SELECT code FROM incidents ORDER BY id")]
    assert "wagon_arrived:62340" in codes and "wagon_departed:24120" in codes
    heartbeat = box.state("heartbeat")
    assert heartbeat["standing"] is None and heartbeat["status"] == "running"


def test_wagon_collector_evidence_saves_the_frame_and_an_accepted_number_only(tmp_path, settings):
    from weighbridge.wagon_collector import WagonCollector
    settings.GO2RTC_API_URL = "http://video:1984"
    box = Outbox(tmp_path)
    stop = {"version": 2, "kind": "wagon_stop", "id": "stop-1", "camera": "cam8", "weight_kg": 62340,
            "stable_weight_at": "2026-09-14T04:00:00+00:00", "scale_age_seconds": "0.1", "scale_updated_at": "t1", "still_seconds": 11.0}
    box.put(stop)
    collector = WagonCollector(box)
    collector.tracker.arrived(stop, 0.0)
    jpeg = b"\xff\xd8\xff\xe0" + b"1" * 64
    from io import BytesIO
    payloads = iter([
        {"detections": [{"ocr": {"accepted": False, "number": "2805553"}}]},
        {"detections": [{"ocr": {"accepted": True, "number": "28055531"}}]},
    ])
    with patch("apps.grain.scale._open_request", side_effect=lambda request, timeout: BytesIO(jpeg)), \
            patch("apps.cameras.ai.detect_wagon_plate", side_effect=lambda frame: next(payloads)) as detect, \
            patch.object(collector, "_ocr_wait", lambda seconds: False):
        collector.evidence(stop)
    collector.close()
    event = box.next()
    assert event["photo"] == jpeg and event["photo_error"] == ""
    assert (event["number"], event["number_source"], event["ocr_attempts"]) == ("28055531", "model", 2)
    assert detect.call_count == 2
```

Both tests rely on the `settings` pytest-django fixture (present in this repo's test suite: `pytestmark = pytest.mark.django_db` is not needed because nothing touches the DB, but the `settings` fixture requires Django configured — it is, via `pytest.ini`).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_collector.py -q -p no:cacheprovider -k wagon_collector`
Expected: FAIL with `ModuleNotFoundError: No module named 'weighbridge.wagon_collector'`.

- [ ] **Step 3: Implement the collector**

Create `backend/weighbridge/wagon_collector.py`:

```python
"""Wagon-scale collector: one stop under the unloading arch = one intake wagon.

Run: python -m weighbridge.wagon_collector. Only the local SQLite volume and
network access to the wagon scale, the camera PC and the video relay are
needed; no database, Redis or web server.
"""

import fcntl
import os
import signal
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.weighbridge_settings")

from django.conf import settings
from rest_framework.exceptions import APIException
from apps.cameras import ai
from apps.grain import scale
from .outbox import Outbox, is_busy
from .wagon_stop import StopTracker
from .writer import OutboxWriter

SCALE_KEY = "wagon"


class WagonCollector:
    def __init__(self, box):
        self.box = box
        self.camera = settings.WAGON_ARCH_CAMERA
        self.tracker = StopTracker(
            still_seconds=settings.WAGON_ARCH_STILL_SECONDS,
            stable_seconds=settings.WAGON_ARCH_STABLE_SECONDS,
            tolerance=settings.WAGON_ARCH_STABLE_TOLERANCE_KG,
            empty_max=settings.WAGON_ARCH_EMPTY_MAX_KG,
            rise_kg=settings.WAGON_ARCH_NEXT_WAGON_RISE_KG,
            motion_max_age=settings.WAGON_ARCH_MOTION_MAX_AGE_SECONDS,
        )
        self.writer = OutboxWriter(box)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wagon-evidence")
        self.evidence_future = None
        self.stop_event = threading.Event()
        self.status = "starting"
        self.last_scale = 0.0
        self.last_queue_error = 0.0

    # -- evidence ---------------------------------------------------------

    def frame(self):
        """One JPEG from the relay, or None. Never raises: evidence is best effort."""
        src = urllib.parse.urlencode({"src": self.camera + "main"})
        request = urllib.request.Request(settings.GO2RTC_API_URL.rstrip("/") + "/api/frame.jpeg?" + src)
        try:
            with scale._open_request(request, timeout=4) as response:
                value = response.read(4 * 1024 * 1024 + 1)
        except (OSError, ValueError):
            return None
        if len(value) > 4 * 1024 * 1024 or not value.startswith(b"\xff\xd8"):
            return None
        return value

    def _ocr_wait(self, seconds):
        """True when the process is stopping; False after the pause."""
        return self.stop_event.wait(seconds)

    def evidence(self, stop):
        key = stop["id"]
        photo = None
        for _ in range(2):
            photo = self.frame()
            if photo is not None:
                break
        self.writer.enqueue("finish", key, "photo", photo=photo,
                            updates={"photo_error": "" if photo else "snapshot_unavailable"})
        number, source, error, recognition, attempts = "", "", "snapshot_unavailable", None, 0
        frame = photo
        while frame is not None and attempts < settings.WAGON_ARCH_OCR_MAX_ATTEMPTS:
            attempts += 1
            try:
                payload = ai.detect_wagon_plate(frame)
            except (ai.AiUnavailable, ai.AiError):
                error = "recognition_unavailable"
            else:
                number = ai.accepted_plate_number(payload)
                recognition = {"detections": [
                    {k: v for k, v in (d.get("ocr") or {}).items() if k in {"accepted", "number", "confidence"}}
                    for d in payload.get("detections") or [] if isinstance(d, dict)
                ]}
                error = "" if number else ("number_unreadable" if payload.get("detections") else "plate_not_found")
                if number:
                    source = "model"
                    break
            standing = self.tracker.standing
            if standing is None or standing["id"] != key or self._ocr_wait(settings.WAGON_ARCH_OCR_RETRY_SECONDS):
                break
            frame = self.frame() or frame
        self.writer.enqueue("finish", key, "ocr", updates={
            "number": number, "number_source": source, "recognition": recognition,
            "recognition_error": error, "ocr_attempts": attempts,
            "recognition_finished_at": datetime.now(timezone.utc).isoformat(),
        })

    def start_evidence(self, stop):
        if self.evidence_future is not None and not self.evidence_future.done():
            # One OCR slot: a stop that arrives while the previous stop's
            # retries still run gets its own attempt only after they end.
            previous = self.evidence_future
            self.evidence_future = self.pool.submit(lambda: (previous.result(), self.evidence(stop)))
            return
        self.evidence_future = self.pool.submit(self.evidence, stop)

    # -- polling ----------------------------------------------------------

    def poll(self):
        try:
            self._poll()
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            if time.monotonic() - self.last_queue_error > 30:
                print("outbox_busy_retry_preserving_stop", flush=True)
                self.last_queue_error = time.monotonic()

    def _observe_motion(self):
        try:
            value = ai.arch_motion(self.camera)
        except (ai.AiUnavailable, ai.AiError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _poll(self):
        self.writer.start()
        now = time.monotonic()
        new_status = "running"
        try:
            observation = scale.read_truck_scale_observation(SCALE_KEY)
        except (APIException, OSError, ValueError):
            observation = scale.ScaleObservation("unavailable", None, False, False, True, None, None)
        if observation.state in {"ready", "unstable"} and observation.weight_kg is not None:
            self.last_scale = now
        elif now - self.last_scale > 5:
            new_status = "hardware_unavailable"
            if self.last_scale:
                self.box.incident("observation_gap")
        motion = self._observe_motion()
        if motion is None and new_status == "running":
            new_status = "camera_unavailable"
        result = self.tracker.observe(observation, motion, now)
        if result is not None and result[0] == "arrival":
            try:
                reading = scale.read_truck_scale(SCALE_KEY)
            except scale.TruckScaleNotReady:
                self.tracker.arrival_rejected()
                reading = None
            if reading is not None:
                if abs(float(reading.weight_kg) - result[1]) > self.tracker.tolerance:
                    self.tracker.arrival_rejected()
                else:
                    weight = int(reading.weight_kg)
                    read_started = datetime.now(timezone.utc)
                    stop = {
                        "version": 2, "kind": "wagon_stop", "id": str(uuid4()), "camera": self.camera,
                        "weight_kg": weight,
                        "stable_weight_at": (read_started - timedelta(seconds=float(reading.age_seconds))).isoformat(),
                        "scale_age_seconds": str(reading.age_seconds), "scale_updated_at": reading.updated_at,
                        "still_seconds": float((motion or {}).get("still_seconds") or 0.0),
                    }
                    self.tracker.arrived(stop, now)
                    self.writer.enqueue("put", stop)
                    self.box.incident(f"wagon_arrived:{weight}")
                    self.start_evidence(stop)
        elif result is not None and result[0] == "departure":
            _, stop, exit_weight, gap = result
            last = self.tracker.last_stable  # already cleared; keep departure fields from the tuple
            departure = {
                "version": 2, "kind": "wagon_departure", "id": str(uuid4()), "stop_id": stop["id"],
                "camera": self.camera, "weight_kg": exit_weight,
                "stable_weight_at": datetime.now(timezone.utc).isoformat() if exit_weight is not None else None,
                "scale_updated_at": None, "departed_at": datetime.now(timezone.utc).isoformat(),
                "motion_gap": bool(gap),
            }
            self.writer.enqueue("put", departure)
            self.writer.enqueue("finish", departure["id"], "photo", updates={"photo_error": ""})
            self.writer.enqueue("finish", departure["id"], "ocr", updates={})
            label = "wagon_departed_unseen" if gap else "wagon_departed"
            self.box.incident(f"{label}:{exit_weight if exit_weight is not None else 'none'}")
        if new_status != self.status:
            self.box.incident(new_status)
            self.status = new_status
        standing = self.tracker.standing
        self.box.state("heartbeat", {
            "updated_at": time.time(), "status": self.status,
            "standing": standing["id"] if standing else None,
            "motion": self.tracker.motion_state, "pending_writes": self.writer.pending(),
        })

    def close(self):
        self.stop_event.set()
        self.pool.shutdown(wait=True)
        drained = self.writer.drain(timeout=10)
        self.writer.shutdown()
        if not drained:
            print("outbox_storage_unavailable_unflushed_evidence", flush=True)


def main():
    directory = Path(os.environ.get("WEIGHBRIDGE_OUTBOX_DIR", "/var/lib/weighbridge-wagon"))
    box = Outbox(directory)
    lock = (directory / "collector.lock").open("a")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    box.recover()
    box.incident("collector_started")
    collector = WagonCollector(box)
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            started = time.monotonic()
            collector.poll()
            stopped.wait(max(0, 1 - (time.monotonic() - started)))
    finally:
        collector.close()


if __name__ == "__main__":
    main()
```

Two details to keep exact: the departure's `stable_weight_at`/`scale_updated_at` should come from the tracker's last stable reading — extend `StopTracker._depart` to return the whole `last_stable` tuple instead of only its weight if you prefer, and then fill `stable_weight_at` from its observed time; the test only asserts `weight_kg`, `stop_id`, `motion_gap`. Remove the unused `last = ...` line once you decide. The departure event carries no photo, so `next()` must still return it: `finish(..., "photo", photo=None)` keeps the blob `NULL` — that is fine, `next()` returns `"photo": None`.

- [ ] **Step 4: Run the collector tests**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_collector.py -q -p no:cacheprovider`
Expected: PASS. The `settings` fixture assignment `settings.WAGON_ARCH_STILL_SECONDS = 3` must be applied **before** `WagonCollector(box)` (it reads settings in `__init__`) — the test does that.

- [ ] **Step 5: Commit**

```bash
git add backend/weighbridge/wagon_collector.py backend/apps/grain/tests/test_wagon_collector.py
git commit -m "feat(weighbridge): wagon-scale collector recording stops under the arch"
```

---

### Task 5: Deployment — second collector service, relay stream, volumes

**Files:**
- Modify: `deploy/weighbridge/compose.yml` (add `wagon-collector` service and `wagon-outbox` volume)
- Modify: `deploy/weighbridge/go2rtc.yaml` (add `cam8main` stream)
- Modify: `deploy/weighbridge/install.sh:46` (chown both outbox dirs)
- Modify: `docker-compose.prod.yml` (`passage-scale-monitor` volumes ~378, `backend` volumes ~170, top-level `volumes` ~568, env anchor ~80 for `WAGON_SCALE_API_URL` and `WAGON_ARCH_*`)
- Modify: `docker-compose.yml` (dev env pass-through), `.env.example`
- Modify: `deploy/weighbridge/README.md`
- Test: `deploy/tests/test_production_hardening.py` (append a compose invariant)

**Interfaces:**
- Produces: service `asyl-weighbridge-wagon-collector-1`, volume `asyl-weighbridge-wagon-outbox`, mounted at `/var/lib/weighbridge-wagon` in `wagon-collector` (rw), `passage-scale-monitor` (rw) and `backend` (ro); relay stream `cam8main`.

- [ ] **Step 1: Write the failing hardening test**

Append to `deploy/tests/test_production_hardening.py` (follow its existing YAML-loading helpers; it already parses `docker-compose.prod.yml`):

```python
def test_wagon_outbox_is_shared_by_collector_monitor_and_backend():
    prod = load_compose("docker-compose.prod.yml")
    weighbridge = load_compose("deploy/weighbridge/compose.yml")
    assert prod["volumes"]["weighbridge-wagon-outbox"] == {"name": "asyl-weighbridge-wagon-outbox"}
    assert "weighbridge-wagon-outbox:/var/lib/weighbridge-wagon" in prod["services"]["passage-scale-monitor"]["volumes"]
    assert "weighbridge-wagon-outbox:/var/lib/weighbridge-wagon:ro" in prod["services"]["backend"]["volumes"]
    wagon = weighbridge["services"]["wagon-collector"]
    assert wagon["command"] == ["python", "-m", "weighbridge.wagon_collector"]
    assert wagon["environment"]["WEIGHBRIDGE_OUTBOX_DIR"] == "/var/lib/weighbridge-wagon"
    assert wagon["volumes"] == ["wagon-outbox:/var/lib/weighbridge-wagon"]
    assert weighbridge["volumes"]["wagon-outbox"] == {"external": True, "name": "asyl-weighbridge-wagon-outbox"}
    assert "cam8main" in load_yaml("deploy/weighbridge/go2rtc.yaml")["streams"]
```

(Use the module's actual helper names for loading YAML; if none exist, add `load_compose`/`load_yaml` using `yaml.safe_load` on the repo-relative path as the other tests in that file do.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/pytest ../deploy/tests/test_production_hardening.py -q -p no:cacheprovider -k wagon_outbox`
Expected: FAIL with `KeyError: 'weighbridge-wagon-outbox'`.

- [ ] **Step 3: Add the service, stream and volumes**

`deploy/weighbridge/compose.yml` — add after the `collector` service:

```yaml
  wagon-collector:
    image: ${WEIGHBRIDGE_IMAGE_REF:?Pin collector image during installation}
    entrypoint: []
    command: [python, -m, weighbridge.wagon_collector]
    environment:
      DJANGO_SETTINGS_MODULE: config.weighbridge_settings
      WEIGHBRIDGE_OUTBOX_DIR: /var/lib/weighbridge-wagon
      WAGON_SCALE_API_URL: ${WAGON_SCALE_API_URL-http://vesyv:8000/api/v1/weight}
      TRUCK_SCALE_TIMEOUT_SECONDS: "2"
      TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS: "2"
      TRUCK_SCALE_MAX_AGE_SECONDS: "5"
      AI_SERVICE_URL: ${AI_SERVICE_URL:-${CAMERA_AI_URL:-}}
      AI_SERVICE_API_KEY: ${AI_SERVICE_API_KEY:-${CAMERA_AI_KEY:-}}
      WAGON_ARCH_CAMERA: ${WAGON_ARCH_CAMERA:-cam8}
      WAGON_ARCH_STILL_SECONDS: ${WAGON_ARCH_STILL_SECONDS:-10}
      WAGON_ARCH_STABLE_TOLERANCE_KG: ${WAGON_ARCH_STABLE_TOLERANCE_KG:-100}
      WAGON_ARCH_NEXT_WAGON_RISE_KG: ${WAGON_ARCH_NEXT_WAGON_RISE_KG:-5000}
      GO2RTC_API_URL: http://video:1984
    volumes:
      - wagon-outbox:/var/lib/weighbridge-wagon
    init: true
    restart: unless-stopped
    stop_grace_period: 30s
    logging: *logging
    healthcheck:
      test: [CMD, python, -m, weighbridge.healthcheck]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 15s
```

and under `volumes:`:

```yaml
  wagon-outbox:
    external: true
    name: asyl-weighbridge-wagon-outbox
```

`deploy/weighbridge/go2rtc.yaml` — under `streams:` add `cam8main: rtsp://${CAMERA_USER}:${CAMERA_PASS}@${CAMERA_HOST}:8554/cam8` (do not add it to `preload`; frames are fetched on demand).

`deploy/weighbridge/install.sh` line 46 — change the chown command to `'chown app:app /var/lib/weighbridge /var/lib/weighbridge-wagon'`.

`docker-compose.prod.yml`:
- `backend` volumes: add `- weighbridge-wagon-outbox:/var/lib/weighbridge-wagon:ro`
- `passage-scale-monitor` volumes: add `- weighbridge-wagon-outbox:/var/lib/weighbridge-wagon`
- top-level `volumes`: add `weighbridge-wagon-outbox:\n    name: asyl-weighbridge-wagon-outbox`
- env anchor: replace the `WAGON_SCALE_API_URL: ${WAGON_SCALE_API_URL-}` comment «Весов вагонов пока нет» with «Вагонные весы под аркой (vesyv, CAS Weight API)»; add `WAGON_ARCH_AUTOMATION_ENABLED: ${WAGON_ARCH_AUTOMATION_ENABLED:-0}` and `WAGON_ARCH_CAMERA: ${WAGON_ARCH_CAMERA:-cam8}`.

`docker-compose.yml` (dev) — add the same two `WAGON_ARCH_*` keys next to the `VEHICLE_PLATE_AUTO_SCALE_*` block. `.env.example` — document `WAGON_SCALE_API_URL=http://vesyv:8000/api/v1/weight`, `WAGON_ARCH_AUTOMATION_ENABLED=0`, `WAGON_ARCH_CAMERA=cam8`, `WAGON_ARCH_STILL_SECONDS=10`, `WAGON_ARCH_STABLE_TOLERANCE_KG=100`, `WAGON_ARCH_NEXT_WAGON_RISE_KG=5000`.

`deploy/weighbridge/README.md` — add a section «Wagon collector»: second service in the same project, own volume, no activation marker; the CRM imports only with `WAGON_ARCH_AUTOMATION_ENABLED=1`; it needs `cam8main` in the relay and `GET /cameras/cam8/arch-motion` on the camera PC (Part 1); the stop/departure event shapes from Global Constraints; how to inspect: `docker exec asyl-weighbridge-wagon-collector-1 python -c "import sqlite3; ..."`.

- [ ] **Step 4: Run the hardening tests**

Run: `cd backend && .venv/bin/pytest ../deploy/tests -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/weighbridge/compose.yml deploy/weighbridge/go2rtc.yaml deploy/weighbridge/install.sh deploy/weighbridge/README.md docker-compose.prod.yml docker-compose.yml .env.example deploy/tests/test_production_hardening.py
git commit -m "deploy(weighbridge): wagon collector service, cam8 relay stream and second outbox volume"
```

---

## Rollout

1. Push `main`; CI + deploy create the `asyl-weighbridge-wagon-outbox` volume (declared in `docker-compose.prod.yml`) and mount it into the monitor/backend.
2. Run «Activate independent weighbridge collector» with `upgrade=true` on an empty truck scale: `install.sh upgrade` brings up both services of the `asyl-weighbridge` project (the wagon collector starts idle; without Part 1 on the camera PC it logs `camera_unavailable` and records nothing).
3. Verify from the server: `docker exec asyl-weighbridge-wagon-collector-1 python -m weighbridge.healthcheck; echo $?` → 0; the heartbeat shows `"motion": "still"|"moving"` once Part 1 is deployed and the zone is drawn; `WAGON_SCALE_API_URL` must answer (`raw` non-empty, `stale: false`) — on 2026-09-14 the wagon indicator still sent nothing readable.

## Self-review

- Spec coverage: per-second scale + motion poll, UUID per stop, full weight + frame + number at arrival, exit weight before departure, motion unknown never closes a stop, heavier wagon during a motion gap departs the old stop, incidents for every transition, separate service/volume, upgrade path via the activation workflow. ✔ Re-positioning of the same wagon and the "weight did not fall" rule are CRM-side (Part 3), as the spec places them.
- Type consistency: `StopTracker.observe` tuple shapes match `WagonCollector._poll`; event keys match Global Constraints and the Part 3 importer contract; `OutboxWriter` API (`enqueue/start/pending/drain/shutdown`) used identically by both collectors. ✔
- No placeholders. ✔
