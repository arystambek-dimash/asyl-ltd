from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cv_service.app import create_app
from cv_service.cloud_conveyor import CloudConveyorObserver
from cv_service.contracts import Detection, ProcessorOptions
from cv_service.conveyor import (
    ConveyorConflictError,
    ConveyorReadbackError,
    ConveyorSupervisor,
    ConveyorUnavailableError,
    ModbusTcpClient,
)
from cv_service.processor import CameraProcessor, ProcessorManager
from cv_service.settings import (
    ConveyorControllerSettings,
    Settings,
    parse_conveyor_controllers,
)

KEY = "backend-only-secret"
DIGEST = hashlib.sha256(KEY.encode()).hexdigest()


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def controller(**overrides):
    values = {
        "host": "127.0.0.1",
        "port": 1502,
        "unit_id": 7,
        "register_type": "coil",
        "address": 0x11,
        "on_value": 1,
        "off_value": 0,
        "feedback_register_type": "coil",
        "feedback_address": 0x12,
        "feedback_on_value": 1,
        "feedback_off_value": 0,
    }
    values.update(overrides)
    return ConveyorControllerSettings(**values)


def settings_for(config=None, **overrides):
    values = {
        "api_key_sha256": DIGEST,
        "model_path": Path("best.pt"),
        "model_device": "cpu",
        "prewarm_timeout": 0.3,
        "conveyor_controllers": {"cam2": config} if config else {},
        "conveyor_heartbeat_seconds": 0.02,
        "conveyor_stale_ai_seconds": 0.25,
        "conveyor_io_timeout_seconds": 0.05,
        "conveyor_command_timeout_seconds": 0.3,
    }
    values.update(overrides)
    return Settings(**values)


def adu(transaction, unit_id, pdu):
    return struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit_id) + pdu


class ScriptedSocket:
    def __init__(self, responses):
        self.responses = bytearray().join(responses)
        self.sent = []
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        if not self.responses:
            return b""
        # Deliberately fragment every response to exercise recv_exact().
        size = min(size, 1)
        result = bytes(self.responses[:size])
        del self.responses[:size]
        return result

    def close(self):
        self.closed = True


def test_modbus_coil_write_then_independent_readback(monkeypatch):
    sock = ScriptedSocket([
        adu(1, 7, b"\x05\x00\x11\xff\x00"),
        adu(2, 7, b"\x01\x01\x01"),
        adu(3, 7, b"\x05\x00\x11\x00\x00"),
        adu(4, 7, b"\x01\x01\x00"),
    ])
    monkeypatch.setattr(
        "cv_service.conveyor.socket.create_connection",
        lambda *_args, **_kwargs: sock,
    )
    client = ModbusTcpClient(controller(), timeout=0.1)

    assert client.set_and_verify(True) is True
    assert client.set_and_verify(False) is False
    assert sock.sent == [
        adu(1, 7, b"\x05\x00\x11\xff\x00"),
        adu(2, 7, b"\x01\x00\x12\x00\x01"),
        adu(3, 7, b"\x05\x00\x11\x00\x00"),
        adu(4, 7, b"\x01\x00\x12\x00\x01"),
    ]


def test_modbus_holding_register_support_and_mismatch_closes_socket(monkeypatch):
    config = controller(
        register_type="holding",
        address=3,
        on_value=9,
        off_value=4,
        feedback_register_type="holding",
        feedback_address=8,
        feedback_on_value=9,
        feedback_off_value=4,
    )
    sock = ScriptedSocket([
        adu(1, 7, b"\x06\x00\x03\x00\x09"),
        adu(2, 7, b"\x03\x02\x00\x04"),
    ])
    monkeypatch.setattr(
        "cv_service.conveyor.socket.create_connection",
        lambda *_args, **_kwargs: sock,
    )

    with pytest.raises(ConveyorReadbackError, match="mismatch") as error:
        ModbusTcpClient(config, timeout=0.1).set_and_verify(True)
    assert error.value.feedback is False
    assert sock.closed is True
    assert sock.sent == [
        adu(1, 7, b"\x06\x00\x03\x00\x09"),
        adu(2, 7, b"\x03\x00\x08\x00\x01"),
    ]


def test_controller_json_is_strict_and_rejects_duplicate_physical_output():
    parsed = parse_conveyor_controllers(
        '{"cam2":{"host":"10.0.0.2","register_type":"holding",'
        '"address":12,"on_value":7,"off_value":3,'
        '"feedback_address":13}}'
    )
    assert parsed["cam2"].register_type == "holding"
    assert parsed["cam2"].feedback_address == 13
    with pytest.raises(ValueError, match="multiple cameras"):
        parse_conveyor_controllers(
            '{"cam2":{"host":"10.0.0.2","address":1,"feedback_address":2},'
            '"cam3":{"host":"10.0.0.2","address":1,"feedback_address":3}}'
        )
    with pytest.raises(ValueError, match="unknown"):
        parse_conveyor_controllers(
            '{"cam2":{"host":"10.0.0.2","address":1,'
            '"feedback_address":2,"url":"evil"}}'
        )
    with pytest.raises(ValueError, match="feedback_address.*required"):
        parse_conveyor_controllers(
            '{"cam2":{"host":"10.0.0.2","address":1}}'
        )
    with pytest.raises(ValueError, match="separate input/register"):
        parse_conveyor_controllers(
            '{"cam2":{"host":"10.0.0.2","address":1,'
            '"feedback_address":1}}'
        )


@pytest.mark.parametrize(
    ("cam3_address", "cam3_feedback"),
    [
        (3, 2),  # feedback-feedback
        (2, 3),  # command-feedback
        (3, 1),  # feedback-command
    ],
)
def test_controller_points_are_globally_unique_and_disjoint(
    cam3_address, cam3_feedback,
):
    payload = {
        "cam2": {
            "host": "plc.internal",
            "port": 1502,
            "unit_id": 7,
            "address": 1,
            "feedback_address": 2,
        },
        "cam3": {
            "host": "PLC.INTERNAL",
            "port": 1502,
            "unit_id": 7,
            "address": cam3_address,
            "feedback_address": cam3_feedback,
        },
    }

    with pytest.raises(ValueError, match="physical Modbus point.*multiple cameras"):
        parse_conveyor_controllers(json.dumps(payload))


def test_controller_point_identity_uses_full_modbus_tuple():
    base = {
        "host": "plc-a.internal",
        "port": 1502,
        "unit_id": 7,
        "register_type": "coil",
        "address": 1,
        "feedback_register_type": "coil",
        "feedback_address": 2,
    }
    payload = {
        "cam2": base,
        "cam3": {**base, "host": "plc-b.internal"},
        "cam4": {**base, "port": 1503},
        "cam5": {**base, "unit_id": 8},
        "cam6": {
            **base,
            "register_type": "holding",
            "feedback_register_type": "holding",
        },
    }

    assert set(parse_conveyor_controllers(json.dumps(payload))) == set(payload)


class RecordingModbus:
    def __init__(self, _config, _timeout):
        self.calls = []
        self.closed = False
        self.fail_next_on = False
        self.fail_all = False
        self.block_on = False
        self.on_started = threading.Event()
        self.release_on = threading.Event()
        self.feedback_override = None
        self.lock = threading.Condition()

    def set_and_verify(self, enabled):
        with self.lock:
            self.calls.append(enabled)
            feedback_override = self.feedback_override
            self.lock.notify_all()
        if self.fail_all:
            raise OSError("controller disconnected")
        if enabled and self.block_on:
            self.on_started.set()
            assert self.release_on.wait(timeout=1)
        if enabled and self.fail_next_on:
            self.fail_next_on = False
            raise OSError("controller disconnected")
        feedback = enabled if feedback_override is None else feedback_override
        if feedback is not enabled:
            raise ConveyorReadbackError(
                "conveyor write/readback mismatch", feedback=feedback,
            )
        return feedback

    def set_feedback_override(self, feedback):
        with self.lock:
            self.feedback_override = feedback

    def wait_for_call_count(self, enabled, count, timeout=1.0):
        with self.lock:
            assert self.lock.wait_for(
                lambda: self.calls.count(enabled) >= count,
                timeout=timeout,
            )

    def close(self):
        self.closed = True


def supervisor(*, monotonic=time.monotonic, **settings_overrides):
    clients = []

    def factory(config, timeout):
        client = RecordingModbus(config, timeout)
        clients.append(client)
        return client

    instance = ConveyorSupervisor(
        settings_for(controller(), **settings_overrides),
        client_factory=factory,
        monotonic=monotonic,
    )
    wait_until(lambda: instance.status("cam2")["feedback"] == 0)
    return instance, clients[0]


def test_confirmed_off_is_reasserted_on_every_heartbeat():
    control, relay = supervisor(conveyor_heartbeat_seconds=0.01)
    confirmed_off_calls = relay.calls.count(False)

    relay.wait_for_call_count(False, confirmed_off_calls + 2)

    status = control.status("cam2")
    assert status["state"] == "off"
    assert status["online"] is True
    assert status["feedback"] == 0
    control.close()


def test_off_heartbeat_detects_stuck_on_and_recovers():
    control, relay = supervisor(conveyor_heartbeat_seconds=0.01)

    relay.set_feedback_override(True)
    calls_before_fault = relay.calls.count(False)
    relay.wait_for_call_count(False, calls_before_fault + 1)
    wait_until(lambda: control.status("cam2")["state"] == "fault")
    fault = control.status("cam2")
    assert fault["desired"] == 0
    assert fault["feedback"] == 1
    assert fault["online"] is True
    assert "mismatch" in fault["error"]

    relay.set_feedback_override(None)
    calls_before_recovery = relay.calls.count(False)
    relay.wait_for_call_count(False, calls_before_recovery + 1)
    wait_until(lambda: control.status("cam2")["state"] == "off")
    recovered = control.status("cam2")
    assert recovered["feedback"] == 0
    assert recovered["error"] is None
    control.close()


def test_unexpected_on_while_armed_is_terminal_even_after_off_recovers():
    control, relay = supervisor(conveyor_heartbeat_seconds=0.01)
    control.arm("cam2", 10, 5)

    relay.set_feedback_override(True)
    calls_before_fault = relay.calls.count(False)
    relay.wait_for_call_count(False, calls_before_fault + 1)
    wait_until(lambda: control.status("cam2")["terminal"] is True)
    fault = control.status("cam2")
    assert fault["stop_reason"] == "controller_fault"
    assert fault["desired"] == 0

    relay.set_feedback_override(None)
    calls_before_recovery = relay.calls.count(False)
    relay.wait_for_call_count(False, calls_before_recovery + 1)
    wait_until(lambda: control.status("cam2")["feedback"] == 0)
    recovered = control.status("cam2")
    assert recovered["terminal"] is True
    assert recovered["stop_reason"] == "controller_fault"
    with pytest.raises(ConveyorConflictError, match="terminal"):
        control.run("cam2", 10)
    control.close()


def test_supervisor_boot_arm_run_heartbeat_target_and_shutdown_off():
    control, relay = supervisor(conveyor_stale_ai_seconds=1.0)
    assert relay.calls[0] is False  # boot OFF
    control.arm("cam2", 11, 2)
    control.observe_ai("cam2", 11, 0)
    assert control.status("cam2")["state"] == "armed"

    control.run("cam2", 11)
    running = control.status("cam2")
    assert running["state"] == "running"
    assert running["online"] is True
    assert running["feedback"] == 1
    wait_until(lambda: relay.calls.count(True) >= 2)  # lease heartbeat

    control.observe_ai("cam2", 11, 2)
    wait_until(lambda: control.status("cam2")["state"] == "goal_reached")
    status = control.status("cam2")
    assert status["desired"] == 0
    assert status["feedback"] == 0
    assert status["stop_reason"] == "target_reached"
    assert status["terminal"] is True
    with pytest.raises(ConveyorConflictError, match="terminal"):
        control.run("cam2", 11)

    control.close()
    assert relay.calls[-1] is False  # shutdown OFF is written again
    assert relay.closed is True


def test_stale_ai_stops_and_never_auto_resumes_same_session():
    control, relay = supervisor(
        conveyor_stale_ai_seconds=0.06,
        conveyor_heartbeat_seconds=0.01,
    )
    control.arm("cam2", 12, 10)
    control.observe_ai("cam2", 12, 0)
    control.run("cam2", 12)

    wait_until(
        lambda: control.status("cam2")["stop_reason"] == "stale_ai",
        timeout=0.5,
    )
    wait_until(
        lambda: relay.calls[-1] is False
        and control.status("cam2")["state"] == "off"
    )
    assert relay.calls[-1] is False
    control.observe_ai("cam2", 12, 1)  # recovery alone must not restart
    with pytest.raises(ConveyorConflictError, match="terminal"):
        control.run("cam2", 12)
    control.close()


def test_no_counter_progress_latches_terminal_off_even_with_fresh_ai():
    clock = [0.0]
    control, relay = supervisor(
        monotonic=lambda: clock[0],
        conveyor_stale_ai_seconds=1000.0,
        conveyor_no_progress_seconds=15.0,
        conveyor_max_run_seconds=300.0,
        conveyor_heartbeat_seconds=0.01,
    )
    control.arm("cam2", 121, 10)
    control.observe_ai("cam2", 121, 0, captured_at=0.0)
    control.run("cam2", 121)

    clock[0] = 15.0
    # Empty/black-camera inference is still fresh, but total made no progress.
    control.observe_ai("cam2", 121, 0, captured_at=15.0)
    wait_until(lambda: control.status("cam2")["stop_reason"] == "no_progress")
    wait_until(lambda: relay.calls[-1] is False)
    status = control.status("cam2")
    assert status["terminal"] is True
    assert status["desired"] == 0
    assert status["run_elapsed_seconds"] == 15.0
    assert status["progress_idle_seconds"] == 15.0
    assert "no progress" in status["error"]

    control.observe_ai("cam2", 121, 1, captured_at=15.0)
    with pytest.raises(ConveyorConflictError, match="terminal"):
        control.run("cam2", 121)
    control.close()


def test_absolute_max_runtime_latches_terminal_despite_counter_progress():
    clock = [0.0]
    control, relay = supervisor(
        monotonic=lambda: clock[0],
        conveyor_stale_ai_seconds=1000.0,
        conveyor_no_progress_seconds=30.0,
        conveyor_max_run_seconds=300.0,
        conveyor_heartbeat_seconds=0.01,
    )
    control.arm("cam2", 122, 100)
    control.observe_ai("cam2", 122, 0, captured_at=0.0)
    control.run("cam2", 122)

    clock[0] = 300.0
    control.observe_ai("cam2", 122, 1, captured_at=300.0)
    wait_until(lambda: control.status("cam2")["stop_reason"] == "max_runtime")
    wait_until(lambda: relay.calls[-1] is False)
    status = control.status("cam2")
    assert status["terminal"] is True
    assert status["desired"] == 0
    assert status["run_elapsed_seconds"] == 300.0
    assert status["progress_idle_seconds"] == 0.0
    assert "maximum continuous" in status["error"]

    control.observe_ai("cam2", 122, 2, captured_at=300.0)
    with pytest.raises(ConveyorConflictError, match="terminal"):
        control.run("cam2", 122)
    control.close()


def test_manual_stop_latches_before_delayed_start():
    control, _relay = supervisor()
    control.arm("cam2", 13, 10)
    control.observe_ai("cam2", 13, 0)
    control.stop("cam2", 13)

    assert control.status("cam2")["state"] == "off"
    assert control.status("cam2")["stop_reason"] == "manual_stop"
    with pytest.raises(ConveyorConflictError, match="terminal"):
        control.run("cam2", 13)
    with pytest.raises(ConveyorConflictError, match="stale"):
        control.run("cam2", 999)
    control.close()


def test_repeated_stop_requires_fresh_off_readback_not_cached_success():
    control, relay = supervisor(conveyor_command_timeout_seconds=0.08)
    control.arm("cam2", 131, 10)
    control.observe_ai("cam2", 131, 0)
    control.run("cam2", 131)
    control.stop("cam2", 131)
    successful_calls = len(relay.calls)

    relay.fail_all = True
    with pytest.raises(ConveyorUnavailableError, match="not verified"):
        control.stop("cam2", 131)
    assert len(relay.calls) > successful_calls
    assert control.status("cam2")["online"] is False
    assert control.status("cam2")["feedback"] is None

    relay.fail_all = False
    wait_until(lambda: control.status("cam2")["feedback"] == 0)
    control.close()


def test_target_off_wins_over_in_flight_on_write():
    control, relay = supervisor()
    control.arm("cam2", 14, 1)
    control.observe_ai("cam2", 14, 0)
    relay.block_on = True
    outcome = []

    def run():
        try:
            control.run("cam2", 14)
        except Exception as exc:  # noqa: BLE001 - assertion captures race result
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert relay.on_started.wait(timeout=1)
    control.observe_ai("cam2", 14, 1)
    relay.release_on.set()
    thread.join(timeout=1)

    wait_until(
        lambda: relay.calls[-1] is False
        and control.status("cam2")["state"] == "goal_reached"
    )
    assert relay.calls[-1] is False
    assert control.status("cam2")["state"] == "goal_reached"
    assert outcome and isinstance(outcome[0], ConveyorConflictError)
    control.close()


def test_emergency_off_wins_over_in_flight_start_and_fences_session():
    control, relay = supervisor()
    control.arm("cam2", 142, 5)
    control.observe_ai("cam2", 142, 0)
    relay.block_on = True
    start_errors = []
    emergency_errors = []

    def run():
        try:
            control.run("cam2", 142)
        except Exception as exc:  # noqa: BLE001 - assertion captures race result
            start_errors.append(exc)

    def emergency_stop():
        try:
            control.emergency_stop("cam2", 142)
        except Exception as exc:  # noqa: BLE001 - assertion captures race result
            emergency_errors.append(exc)

    start_thread = threading.Thread(target=run)
    start_thread.start()
    assert relay.on_started.wait(timeout=1)

    stop_thread = threading.Thread(target=emergency_stop)
    stop_thread.start()
    wait_until(
        lambda: control.status("cam2")["stop_reason"] == "emergency_stop"
        and control.status("cam2")["terminal"] is True
    )
    relay.release_on.set()
    start_thread.join(timeout=1)
    stop_thread.join(timeout=1)

    wait_until(
        lambda: relay.calls[-1] is False
        and control.status("cam2")["feedback"] == 0
    )
    assert emergency_errors == []
    assert start_errors and isinstance(start_errors[0], ConveyorConflictError)
    with pytest.raises(ConveyorConflictError, match="emergency-stopped"):
        control.arm("cam2", 142, 5)

    # Backend reconciliation releases the old binding, but its id remains
    # fenced. A genuinely new database session can then be prepared.
    control.release("cam2", 142)
    with pytest.raises(ConveyorConflictError, match="emergency-stopped"):
        control.arm("cam2", 142, 5)
    control.arm("cam2", 143, 5)
    assert control.status("cam2")["state"] == "armed"
    control.close()


def test_concurrent_duplicate_run_shares_one_in_flight_command():
    control, relay = supervisor()
    control.arm("cam2", 141, 5)
    control.observe_ai("cam2", 141, 0)
    relay.block_on = True
    errors = []

    def run():
        try:
            control.run("cam2", 141)
        except Exception as exc:  # noqa: BLE001 - concurrency assertion
            errors.append(exc)

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert relay.on_started.wait(timeout=1)
    second.start()
    time.sleep(0.02)
    relay.release_on.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert errors == []
    assert control.status("cam2")["state"] == "running"
    assert control.status("cam2")["terminal"] is False
    control.stop("cam2", 141)
    control.close()


def test_on_io_fault_latches_terminal_and_requests_off():
    control, relay = supervisor()
    control.arm("cam2", 15, 5)
    control.observe_ai("cam2", 15, 0)
    relay.fail_next_on = True

    with pytest.raises(ConveyorConflictError, match="terminal"):
        control.run("cam2", 15)
    wait_until(lambda: control.status("cam2")["feedback"] == 0)
    assert control.status("cam2")["stop_reason"] == "controller_fault"
    assert relay.calls[-1] is False
    control.close()


class ImmediateTracker:
    def update(self, detections, *_args):
        return detections


class ObservedConveyor:
    def __init__(self):
        self.updates = []

    def observe_ai(self, camera, session_id, total):
        self.updates.append((camera, session_id, total))


def test_apply_inference_counts_whole_frame_then_latches_target_without_io():
    conveyor = ObservedConveyor()
    processor = CameraProcessor.__new__(CameraProcessor)
    processor.manager = SimpleNamespace(conveyor=conveyor)
    processor.camera = "cam2"
    processor._lock = threading.RLock()
    processor._source_generation = 1
    processor._last_inference_generation = -1
    processor.inferences = 0
    processor.inference_times = deque(maxlen=10)
    processor.frame_latencies = deque(maxlen=10)
    processor.latest_detections = []
    processor.latest_frame_shape = None
    processor.latest_counted = frozenset()
    processor.dropped_frames = 0
    processor.running = True
    processor.mode = "session"
    processor.tracker = ImmediateTracker()
    processor.options = ProcessorOptions()
    processor.settings = settings_for()
    processor.total = 0
    processor.per_color = defaultdict(int)
    processor.confidence_sums = defaultdict(float)
    processor.session_id = 21
    processor.target_total = 2
    processor.goal_reached = False
    processor._session_ai_ready = threading.Event()
    frame = SimpleNamespace(shape=(100, 100, 3))
    detections = [
        Detection(0, 0, 10, 10, 0.8, "Red_50"),
        Detection(10, 0, 20, 10, 0.9, "Red_50"),
        Detection(20, 0, 30, 10, 0.7, "White_25"),
    ]

    processor.apply_inference(
        frame, time.monotonic(), detections, 1.0, source_generation=1,
    )

    assert processor.total == 3  # coast/overshoot is recorded, never hidden
    assert processor.per_color == {"Red_50": 2, "White_25": 1}
    assert processor.goal_reached is True
    assert processor._session_ai_ready.is_set()
    assert conveyor.updates == [("cam2", 21, 3)]


class ApiModel:
    def metadata(self):
        return {"id": "best.pt", "device": "cpu", "classes": ["Red_50"]}

    def predict(self, _frame):
        return []


class ApiMedia:
    def validate_source(self, _camera, _source):
        pass

    def camera_inventory(self):
        return {"cam2": {"cam": "cam2"}}

    def device_inventory(self):
        return []


class ApiProcessor:
    def __init__(self, manager, camera, options):
        self.manager = manager
        self.camera = camera
        self.options = options
        self.source_stream = manager.settings.source_stream(camera, options.source)
        self.running = False
        self.mode = "idle"
        self.session_id = None
        self.target_total = None
        self.conveyor_transport = "direct"
        self.goal_reached = False
        self.total = 0

    def configure(self, options):
        self.options = options
        self.source_stream = self.manager.settings.source_stream(
            self.camera, options.source,
        )

    def start_controlled_session(
        self, options, *, session_id, target_total,
        conveyor_transport="direct",
    ):
        self.options = options
        self.running = True
        self.mode = "session"
        self.session_id = session_id
        self.target_total = target_total
        self.conveyor_transport = conveyor_transport
        self.goal_reached = False
        self.total = 0

    def wait_for_session_ai(self, session_id):
        self.manager.observe_conveyor_ai(
            self.camera, session_id, self.total, time.monotonic(),
        )

    def control_session(self):
        return self.session_id, self.target_total, self.goal_reached

    def status(self):
        base_status = (
            self.manager.cloud_conveyor.status(self.camera)
            if self.conveyor_transport == "cloud"
            else self.manager.conveyor.status(self.camera)
        )
        conveyor_status = {
            **base_status,
            "session_id": self.session_id,
            "target_total": self.target_total,
            "conveyor_transport": self.conveyor_transport,
            "goal_reached": self.goal_reached,
        }
        return {
            "cam": self.camera,
            "running": self.running,
            "mode": self.mode,
            "recording": self.mode == "session",
            "processor_alive": True,
            "warm": not self.running,
            "stream": f"{self.camera}ai",
            "source": self.options.source,
            "line": self.options.line or self.manager.settings.default_line,
            "direction": self.options.direction,
            "total": self.total,
            "session_id": self.session_id,
            "target_total": self.target_total,
            "conveyor_transport": self.conveyor_transport,
            "remaining": self.target_total,
            "goal_reached": self.goal_reached,
            "conveyor": conveyor_status,
            "last_frame_at": None,
            "error": None,
        }

    def close(self):
        pass


def api_manager(
    settings, conveyor_supervisor=None, cloud_conveyor_observer=None,
):
    return ProcessorManager(
        settings,
        ApiModel(),
        ApiMedia(),
        "libx264",
        processor_factory=ApiProcessor,
        conveyor_supervisor=conveyor_supervisor,
        cloud_conveyor_observer=cloud_conveyor_observer,
    )


def auth():
    return {"X-Api-Key": KEY}


def test_controlled_api_is_strict_and_unconfigured_rollout_stays_counting_only():
    manager = api_manager(settings_for())
    with TestClient(create_app(manager)) as client:
        assert client.post(
            "/processors/cam2/session",
            json={"session_id": 31, "target_total": 4},
        ).status_code == 401
        assert client.post(
            "/processors/cam2/session", headers=auth(),
            json={"session_id": "31", "target_total": 4},
        ).status_code == 422
        response = client.post(
            "/processors/cam2/session", headers=auth(),
            json={"session_id": 31, "target_total": 4},
        )
        assert response.status_code == 200
        assert response.json()["session_id"] == 31
        assert response.json()["target_total"] == 4
        assert response.json()["conveyor"]["configured"] is False
        assert client.post(
            "/processors/cam2/conveyor/start", headers=auth(),
            json={"session_id": 31},
        ).status_code == 409
        stopped = client.post(
            "/processors/cam2/conveyor/stop", headers=auth(),
            json={"session_id": 31},
        )
        assert stopped.status_code == 200
        assert stopped.json()["running"] is True


def test_cloud_session_only_publishes_observations_and_never_calls_local_run():
    delivered = []

    def transport(_url, _key, payload, _timeout, _context):
        delivered.append(dict(payload))

    settings = settings_for(
        conveyor_cloud_cameras=("cam2",),
        conveyor_cloud_api_key="cloud-token",
    )
    cloud = CloudConveyorObserver(settings, transport=transport)
    manager = api_manager(settings, cloud_conveyor_observer=cloud)
    with TestClient(create_app(manager)) as client:
        legacy = client.post(
            "/processors/cam2/session", headers=auth(),
            json={"session_id": 40, "target_total": 4},
        )
        assert legacy.status_code == 409

        response = client.post(
            "/processors/cam2/session", headers=auth(),
            json={
                "session_id": 40,
                "target_total": 4,
                "conveyor_transport": "cloud",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["conveyor_transport"] == "cloud"
        assert body["conveyor"]["transport"] == "cloud"
        assert body["conveyor"]["direct_control"] is False
        assert body["conveyor"]["write_readback"] is False
        assert body["conveyor"]["feedback"] is None

        # Camera-PC never owns or energizes cloud output.
        started = client.post(
            "/processors/cam2/conveyor/start", headers=auth(),
            json={"session_id": 40},
        )
        assert started.status_code == 409

        deadline = time.monotonic() + 1
        while not delivered and time.monotonic() < deadline:
            time.sleep(0.005)
        assert delivered[0]["session_id"] == 40
        assert delivered[0]["terminal_reason"] is None

        invalid = client.post(
            "/processors/cam2/session", headers=auth(),
            json={
                "session_id": 40,
                "target_total": 4,
                "conveyor_transport": "serial",
            },
        )
        assert invalid.status_code == 422


def test_controlled_api_arm_run_and_terminal_stop_return_full_status():
    control, relay = supervisor(conveyor_stale_ai_seconds=1.0)
    manager = api_manager(
        settings_for(controller(), conveyor_stale_ai_seconds=1.0),
        conveyor_supervisor=control,
    )
    with TestClient(create_app(manager)) as client:
        inventory = client.get("/cameras", headers=auth()).json()
        assert inventory["cameras"][0]["conveyor"]["configured"] is True
        assert inventory["cameras"][0]["conveyor"]["feedback"] == 0
        armed = client.post(
            "/processors/cam2/session", headers=auth(),
            json={"session_id": 32, "target_total": 5},
        )
        assert armed.status_code == 200
        assert armed.json()["conveyor"]["state"] == "armed"
        assert armed.json()["conveyor"]["feedback"] == 0

        running = client.post(
            "/processors/cam2/conveyor/start", headers=auth(),
            json={"session_id": 32},
        )
        assert running.status_code == 200
        running_status = running.json()["conveyor"]
        assert running_status["state"] == "running"
        assert running_status["online"] is True
        assert running_status["feedback"] == 1

        stopped = client.post(
            "/processors/cam2/conveyor/stop", headers=auth(),
            json={"session_id": 32},
        )
        assert stopped.status_code == 200
        assert stopped.json()["cam"] == "cam2"
        assert stopped.json()["conveyor"]["state"] == "off"
        assert stopped.json()["conveyor"]["feedback"] == 0
        delayed = client.post(
            "/processors/cam2/conveyor/start", headers=auth(),
            json={"session_id": 32},
        )
        assert delayed.status_code == 409
    assert relay.calls[-1] is False


def test_emergency_stop_after_restart_needs_no_processor_and_blocks_old_id():
    control, relay = supervisor(conveyor_stale_ai_seconds=1.0)
    manager = api_manager(
        settings_for(controller(), conveyor_stale_ai_seconds=1.0),
        conveyor_supervisor=control,
    )
    with TestClient(create_app(manager)) as client:
        stopped = client.post(
            "/processors/cam2/conveyor/emergency-stop",
            headers=auth(),
            json={"session_id": 77},
        )
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False
        assert stopped.json()["conveyor"]["state"] == "off"
        assert stopped.json()["conveyor"]["feedback"] == 0
        assert stopped.json()["conveyor"]["terminal"] is True

        stale_prepare = client.post(
            "/processors/cam2/session",
            headers=auth(),
            json={"session_id": 77, "target_total": 5},
        )
        assert stale_prepare.status_code == 409

        fresh_prepare = client.post(
            "/processors/cam2/session",
            headers=auth(),
            json={"session_id": 78, "target_total": 5},
        )
        assert fresh_prepare.status_code == 200
        assert fresh_prepare.json()["conveyor"]["state"] == "armed"
    assert relay.calls[-1] is False
