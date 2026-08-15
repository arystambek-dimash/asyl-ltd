from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from .settings import ConveyorControllerSettings, Settings, parse_camera


class ConveyorConflictError(RuntimeError):
    """The command is valid, but not for the current conveyor session."""


class ConveyorUnavailableError(RuntimeError):
    """The relay state could not be proven within the bounded timeout."""


class ModbusProtocolError(RuntimeError):
    pass


class ConveyorReadbackError(ModbusProtocolError):
    def __init__(self, message: str, feedback: bool | None):
        super().__init__(message)
        self.feedback = feedback


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ModbusTcpClient:
    """Minimal Modbus/TCP client for one fail-safe output and its readback."""

    def __init__(self, config: ConveyorControllerSettings, timeout: float):
        self.config = config
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._transaction = 0
        self._lock = threading.Lock()

    def _close_unlocked(self) -> None:
        connection, self._socket = self._socket, None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _connect_unlocked(self) -> socket.socket:
        if self._socket is None:
            self._socket = socket.create_connection(
                (self.config.host, self.config.port), timeout=self.timeout,
            )
            self._socket.settimeout(self.timeout)
        return self._socket

    @staticmethod
    def _recv_exact(connection: socket.socket, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = connection.recv(size - len(chunks))
            if not chunk:
                raise ModbusProtocolError("Modbus connection closed mid-response")
            chunks.extend(chunk)
        return bytes(chunks)

    def _request_unlocked(self, function: int, body: bytes) -> bytes:
        self._transaction = (self._transaction + 1) & 0xFFFF
        transaction = self._transaction
        pdu = bytes((function,)) + body
        request = struct.pack(
            ">HHHB", transaction, 0, len(pdu) + 1, self.config.unit_id,
        ) + pdu
        connection = self._connect_unlocked()
        try:
            connection.sendall(request)
            header = self._recv_exact(connection, 7)
            response_transaction, protocol, length, unit_id = struct.unpack(
                ">HHHB", header,
            )
            if response_transaction != transaction or protocol != 0:
                raise ModbusProtocolError("invalid Modbus response header")
            if unit_id != self.config.unit_id:
                raise ModbusProtocolError("unexpected Modbus unit ID")
            if not 2 <= length <= 254:
                raise ModbusProtocolError("invalid Modbus response length")
            response = self._recv_exact(connection, length - 1)
            response_function = response[0]
            if response_function == (function | 0x80):
                code = response[1] if len(response) > 1 else -1
                raise ModbusProtocolError(f"Modbus exception {code}")
            if response_function != function:
                raise ModbusProtocolError("unexpected Modbus function")
            return response[1:]
        except (OSError, ModbusProtocolError):
            self._close_unlocked()
            raise

    def _write_unlocked(self, enabled: bool) -> None:
        config = self.config
        if config.register_type == "coil":
            function = 5
            raw = config.on_value if enabled else config.off_value
            value = 0xFF00 if raw else 0x0000
        else:
            function = 6
            value = config.on_value if enabled else config.off_value
        body = struct.pack(">HH", config.address, value)
        response = self._request_unlocked(function, body)
        if response != body:
            raise ModbusProtocolError("Modbus write echo does not match request")

    def _read_unlocked(self) -> bool:
        config = self.config
        body = struct.pack(">HH", config.feedback_address, 1)
        if config.feedback_register_type == "coil":
            response = self._request_unlocked(1, body)
            if len(response) != 2 or response[0] != 1:
                raise ModbusProtocolError("invalid coil readback")
            raw = response[1] & 1
        else:
            response = self._request_unlocked(3, body)
            if len(response) != 3 or response[0] != 2:
                raise ModbusProtocolError("invalid holding-register readback")
            raw = struct.unpack(">H", response[1:])[0]
        if raw == config.feedback_on_value:
            return True
        if raw == config.feedback_off_value:
            return False
        raise ConveyorReadbackError(
            f"unexpected conveyor feedback value {raw}", feedback=None,
        )

    def set_and_verify(self, enabled: bool) -> bool:
        with self._lock:
            try:
                self._write_unlocked(enabled)
                feedback = self._read_unlocked()
                if feedback is not enabled:
                    raise ConveyorReadbackError(
                        "conveyor write/readback mismatch", feedback=feedback,
                    )
                return feedback
            except (OSError, ModbusProtocolError):
                self._close_unlocked()
                raise


class ConveyorActuator:
    def __init__(
        self,
        camera: str,
        config: ConveyorControllerSettings,
        *,
        heartbeat_seconds: float,
        stale_ai_seconds: float,
        no_progress_seconds: float,
        max_run_seconds: float,
        command_timeout_seconds: float,
        io_timeout_seconds: float,
        client_factory: Callable[[ConveyorControllerSettings, float], object],
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.camera = camera
        self.config = config
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_ai_seconds = stale_ai_seconds
        self.no_progress_seconds = no_progress_seconds
        self.max_run_seconds = max_run_seconds
        self.command_timeout_seconds = command_timeout_seconds
        self._clock = monotonic
        self._client = client_factory(config, io_timeout_seconds)
        self._condition = threading.Condition(threading.RLock())
        self._closing = False
        self._force_exit = False
        self._closed = False
        self._session_id: int | None = None
        self._target_total: int | None = None
        self._current_total = 0
        self._ai_seen = False
        self._last_ai_at: float | None = None
        self._run_started_at: float | None = None
        self._last_progress_at: float | None = None
        self._run_stopped_at: float | None = None
        self._desired = False
        self._feedback: bool | None = None
        self._online = False
        self._state = "booting"
        self._stop_reason: str | None = "boot"
        self._error: str | None = None
        self._last_seen_at: str | None = None
        self._goal_reached = False
        self._terminal = False
        self._blocked_session_id: int | None = None
        self._launch_attempted = False
        self._generation = 1  # boot always writes and verifies OFF
        self._attempted_generation = 0
        self._confirmed_generation = 0
        self._next_heartbeat = 0.0
        self._thread = threading.Thread(
            target=self._worker_loop,
            name=f"conveyor-{camera}",
            daemon=True,
        )
        self._thread.start()

    def _bump_locked(self) -> int:
        self._generation += 1
        self._condition.notify_all()
        return self._generation

    def _terminal_off_locked(self, reason: str, *, error: str | None = None) -> int:
        self._terminal = True
        self._desired = False
        if self._run_started_at is not None and self._run_stopped_at is None:
            self._run_stopped_at = self._clock()
        self._stop_reason = reason
        self._error = error
        self._state = "stopping"
        return self._bump_locked()

    def _enforce_runtime_limits_locked(self, now: float) -> bool:
        """Latch terminal OFF when an energized run exceeds safety bounds."""

        if not self._desired or self._run_started_at is None:
            return False
        if now - self._run_started_at >= self.max_run_seconds:
            self._terminal_off_locked(
                "max_runtime",
                error="maximum continuous conveyor runtime exceeded",
            )
            return True
        progress_at = self._last_progress_at or self._run_started_at
        if now - progress_at >= self.no_progress_seconds:
            self._terminal_off_locked(
                "no_progress",
                error="counter made no progress while conveyor was running",
            )
            return True
        return False

    def _successful_off_state_locked(self) -> str:
        if self._goal_reached:
            return "goal_reached"
        if self._session_id is not None and not self._terminal:
            return "armed"
        return "off"

    def _wait_for_generation(self, generation: int, desired: bool) -> None:
        deadline = self._clock() + self.command_timeout_seconds
        with self._condition:
            while True:
                same_command = (
                    self._desired is desired
                    and (not desired or self._generation == generation)
                )
                if (
                    same_command
                    and self._confirmed_generation >= generation
                    and self._feedback is desired
                    and self._online
                ):
                    return
                if desired and self._terminal:
                    raise ConveyorConflictError(
                        f"conveyor session is terminal: {self._stop_reason or 'stopped'}"
                    )
                remaining = deadline - self._clock()
                if remaining <= 0:
                    if desired:
                        self._terminal_off_locked(
                            "start_timeout", error="conveyor ON was not verified",
                        )
                    raise ConveyorUnavailableError(
                        f"conveyor {self.camera} state was not verified"
                    )
                self._condition.wait(min(remaining, 0.1))

    def arm(self, session_id: int, target_total: int) -> None:
        with self._condition:
            if self._closing:
                raise ConveyorUnavailableError("conveyor supervisor is closing")
            if self._blocked_session_id == session_id:
                raise ConveyorConflictError(
                    "conveyor session was emergency-stopped and cannot be re-armed"
                )
            if self._session_id is not None and self._session_id != session_id:
                raise ConveyorConflictError("another conveyor session is active")
            if self._session_id == session_id:
                if self._target_total != target_total:
                    raise ConveyorConflictError("target_total is immutable for a session")
                if self._terminal or self._launch_attempted:
                    return
            else:
                self._session_id = session_id
                self._target_total = target_total
                self._current_total = 0
                self._goal_reached = False
                self._terminal = False
                self._launch_attempted = False
                self._ai_seen = False
                self._last_ai_at = None
                self._run_started_at = None
                self._last_progress_at = None
                self._run_stopped_at = None
            self._desired = False
            self._state = "arming"
            self._stop_reason = None
            self._error = None
            generation = self._bump_locked()
        self._wait_for_generation(generation, False)

    def observe_ai(
        self,
        session_id: int,
        total: int,
        captured_at: float | None = None,
    ) -> bool:
        with self._condition:
            if self._closing or session_id != self._session_id:
                return False
            now = self._clock()
            if captured_at is None:
                # Compatibility for internal callers that synchronously
                # observe AI without a queued frame. Production inference
                # always supplies its monotonic capture timestamp.
                captured_at = now
            age = now - captured_at
            if not 0 <= age < self.stale_ai_seconds:
                # A queued result must never extend the watchdog from its
                # completion time. Reject old, non-finite and future capture
                # timestamps without changing totals or readiness.
                return False
            self._ai_seen = True
            self._last_ai_at = captured_at
            previous_total = self._current_total
            self._current_total = max(self._current_total, total)
            if (
                self._desired
                and self._run_started_at is not None
                and captured_at >= self._run_started_at
                and self._current_total > previous_total
            ):
                # Counter progress inherits the frame's capture timestamp, so
                # queued inference cannot extend this independent watchdog.
                self._last_progress_at = captured_at
            if (
                self._target_total is not None
                and self._current_total >= self._target_total
                and not self._goal_reached
            ):
                self._goal_reached = True
                self._terminal_off_locked("target_reached")
            else:
                self._condition.notify_all()
            return True

    def run(self, session_id: int) -> None:
        with self._condition:
            if session_id != self._session_id:
                raise ConveyorConflictError("stale conveyor session_id")
            if self._terminal:
                raise ConveyorConflictError(
                    f"conveyor session is terminal: {self._stop_reason or 'stopped'}"
                )
            now = self._clock()
            if (
                not self._ai_seen
                or self._last_ai_at is None
                or now - self._last_ai_at >= self.stale_ai_seconds
            ):
                raise ConveyorConflictError("AI inference is not fresh for this session")
            if self._desired:
                # A duplicate RUN shares the in-flight generation. Bumping it
                # would make the first caller time out and incorrectly latch
                # a terminal OFF.
                generation = self._generation
            else:
                if self._feedback is not False or not self._online:
                    raise ConveyorUnavailableError("conveyor OFF readback is not verified")
                self._launch_attempted = True
                self._run_started_at = now
                self._last_progress_at = now
                self._run_stopped_at = None
                self._desired = True
                self._state = "starting"
                self._stop_reason = None
                self._error = None
                generation = self._bump_locked()
        self._wait_for_generation(generation, True)

    def stop(self, session_id: int, reason: str = "manual_stop") -> None:
        with self._condition:
            if session_id != self._session_id:
                raise ConveyorConflictError("stale conveyor session_id")
            if not self._terminal:
                generation = self._terminal_off_locked(reason)
            else:
                # Never authorize finalization from cached OFF/online state.
                # Every explicit stop performs a fresh write and readback.
                self._desired = False
                self._state = "stopping"
                generation = self._bump_locked()
        self._wait_for_generation(generation, False)

    def emergency_stop(
        self,
        session_id: int,
        reason: str = "emergency_stop",
    ) -> None:
        """Fresh OFF/readback without requiring a live processor binding.

        Remember the requested session fence so a concurrent/delayed ARM for
        that same DB session cannot clear this terminal stop. A later order has
        a new monotonic DB id and may arm normally after backend reconciliation.
        """
        with self._condition:
            self._blocked_session_id = session_id
            generation = self._terminal_off_locked(reason)
        self._wait_for_generation(generation, False)

    def release(self, session_id: int) -> None:
        with self._condition:
            if session_id != self._session_id:
                raise ConveyorConflictError("stale conveyor session_id")
            self._desired = False
            self._state = "stopping"
            generation = self._bump_locked()
        self._wait_for_generation(generation, False)
        with self._condition:
            if session_id != self._session_id:
                raise ConveyorConflictError("stale conveyor session_id")
            if self._feedback is not False or not self._online:
                raise ConveyorUnavailableError("conveyor OFF readback is not verified")
            self._session_id = None
            self._target_total = None
            self._current_total = 0
            self._ai_seen = False
            self._last_ai_at = None
            self._run_started_at = None
            self._last_progress_at = None
            self._run_stopped_at = None
            self._goal_reached = False
            self._terminal = False
            self._launch_attempted = False
            self._desired = False
            self._state = "off"
            self._condition.notify_all()

    def status(self) -> dict:
        with self._condition:
            now = self._clock()
            run_ended_at = self._run_stopped_at or now
            return {
                "configured": True,
                "enabled": True,
                "session_id": self._session_id,
                "target_total": self._target_total,
                "state": self._state,
                "desired": 1 if self._desired else 0,
                "feedback": (
                    1 if self._feedback is True
                    else 0 if self._feedback is False
                    else None
                ),
                "online": self._online,
                "stop_reason": self._stop_reason,
                "error": self._error,
                "last_seen_at": self._last_seen_at,
                "goal_reached": self._goal_reached,
                "terminal": self._terminal,
                "run_elapsed_seconds": (
                    round(max(0.0, run_ended_at - self._run_started_at), 3)
                    if self._run_started_at is not None else None
                ),
                "progress_idle_seconds": (
                    round(max(0.0, run_ended_at - self._last_progress_at), 3)
                    if self._last_progress_at is not None else None
                ),
                "no_progress_timeout_seconds": self.no_progress_seconds,
                "max_run_seconds": self.max_run_seconds,
            }

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                now = self._clock()
                if self._force_exit:
                    break
                if (
                    self._desired
                    and self._last_ai_at is not None
                    and now - self._last_ai_at >= self.stale_ai_seconds
                ):
                    self._terminal_off_locked(
                        "stale_ai", error="AI inference heartbeat became stale",
                    )
                self._enforce_runtime_limits_locked(now)
                if (
                    self._closing
                    and self._feedback is False
                    and self._confirmed_generation >= self._generation
                ):
                    break
                dirty = self._attempted_generation < self._generation
                # Both states are leases. Reasserting and reading back OFF is
                # as important as refreshing ON: it corrects an out-of-band ON
                # write and exposes a stuck relay/controller loss while idle.
                heartbeat_due = now >= self._next_heartbeat
                if not (dirty or heartbeat_due):
                    deadlines = [now + 0.5, self._next_heartbeat]
                    if self._desired and self._last_ai_at is not None:
                        deadlines.append(self._last_ai_at + self.stale_ai_seconds)
                    if self._desired and self._run_started_at is not None:
                        deadlines.extend((
                            self._run_started_at + self.max_run_seconds,
                            (self._last_progress_at or self._run_started_at)
                            + self.no_progress_seconds,
                        ))
                    self._condition.wait(max(0.01, min(deadlines) - now))
                    continue
                desired = self._desired
                generation = self._generation
            feedback: bool | None = None
            error: str | None = None
            online = False
            try:
                feedback = self._client.set_and_verify(desired)  # type: ignore[attr-defined]
                online = True
            except ConveyorReadbackError as exc:
                feedback = exc.feedback
                online = True
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 - persistent hardware boundary
                error = str(exc) or exc.__class__.__name__

            with self._condition:
                self._feedback = feedback
                self._online = online
                if online:
                    self._last_seen_at = _utc_now()
                # OFF has priority over an ON transaction that was already in
                # flight when a target/manual/fault stop latched.
                superseded = generation != self._generation or desired != self._desired
                if not superseded:
                    self._attempted_generation = max(
                        self._attempted_generation, generation,
                    )
                if not superseded and error is None and feedback is desired:
                    self._confirmed_generation = generation
                    # A successful terminal OFF proves the output is safe, but
                    # must not erase why this accounting session was latched.
                    if not (self._terminal and not desired and self._error):
                        self._error = None
                    self._next_heartbeat = self._clock() + self.heartbeat_seconds
                    if desired:
                        self._state = "running"
                    else:
                        self._state = self._successful_off_state_locked()
                else:
                    if error is not None:
                        self._error = error
                    if desired and not superseded:
                        self._terminal_off_locked(
                            "controller_fault", error=error or "ON readback mismatch",
                        )
                    elif not superseded:
                        if self._session_id is not None and not self._terminal:
                            # Unexpected ON while prepared/armed may already
                            # have moved product. Even if the next OFF retry
                            # succeeds, this order must never auto-start.
                            self._terminal_off_locked(
                                "controller_fault",
                                error=error or "OFF readback mismatch",
                            )
                        else:
                            self._state = "fault"
                    self._next_heartbeat = self._clock() + self.heartbeat_seconds
                self._condition.notify_all()
                if (
                    self._closing
                    and self._feedback is False
                    and self._confirmed_generation >= self._generation
                ):
                    break

        try:
            self._client.close()  # type: ignore[attr-defined]
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closing = True
            self._desired = False
            self._terminal = True
            self._stop_reason = "shutdown"
            self._state = "stopping"
            generation = self._bump_locked()
        try:
            self._wait_for_generation(generation, False)
        except (ConveyorConflictError, ConveyorUnavailableError):
            pass
        with self._condition:
            self._condition.notify_all()
        self._thread.join(timeout=self.command_timeout_seconds + 0.5)
        if self._thread.is_alive():
            with self._condition:
                self._force_exit = True
                self._condition.notify_all()
            try:
                self._client.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001, S110 - shutdown best effort
                pass
            self._thread.join(timeout=0.5)


class ConveyorSupervisor:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[ConveyorControllerSettings, float], object] = ModbusTcpClient,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._actuators = {
            camera: ConveyorActuator(
                camera,
                config,
                heartbeat_seconds=settings.conveyor_heartbeat_seconds,
                stale_ai_seconds=settings.conveyor_stale_ai_seconds,
                no_progress_seconds=settings.conveyor_no_progress_seconds,
                max_run_seconds=settings.conveyor_max_run_seconds,
                command_timeout_seconds=settings.conveyor_command_timeout_seconds,
                io_timeout_seconds=settings.conveyor_io_timeout_seconds,
                client_factory=client_factory,
                monotonic=monotonic,
            )
            for camera, config in settings.conveyor_controllers.items()
        }
        self._closed = False

    @staticmethod
    def _unconfigured_status() -> dict:
        return {
            "configured": False,
            "enabled": False,
            "session_id": None,
            "target_total": None,
            "state": "unconfigured",
            "desired": 0,
            "feedback": None,
            "online": False,
            "stop_reason": None,
            "error": None,
            "last_seen_at": None,
            "goal_reached": False,
            "terminal": False,
            "run_elapsed_seconds": None,
            "progress_idle_seconds": None,
            "no_progress_timeout_seconds": None,
            "max_run_seconds": None,
        }

    def _get(self, camera: str) -> ConveyorActuator:
        camera = parse_camera(camera)
        try:
            return self._actuators[camera]
        except KeyError as exc:
            raise ConveyorConflictError(
                f"no conveyor controller configured for {camera}"
            ) from exc

    def status(self, camera: str) -> dict:
        try:
            camera = parse_camera(camera)
        except ValueError:
            return self._unconfigured_status()
        actuator = self._actuators.get(camera)
        return actuator.status() if actuator is not None else self._unconfigured_status()

    def arm(self, camera: str, session_id: int, target_total: int) -> None:
        self._get(camera).arm(session_id, target_total)

    def observe_ai(
        self,
        camera: str,
        session_id: int,
        total: int,
        captured_at: float | None = None,
    ) -> bool:
        actuator = self._actuators.get(parse_camera(camera))
        if actuator is None:
            return True
        return actuator.observe_ai(session_id, total, captured_at)

    def run(self, camera: str, session_id: int) -> None:
        self._get(camera).run(session_id)

    def stop(self, camera: str, session_id: int, reason: str = "manual_stop") -> None:
        self._get(camera).stop(session_id, reason)

    def emergency_stop(
        self,
        camera: str,
        session_id: int,
        reason: str = "emergency_stop",
    ) -> None:
        self._get(camera).emergency_stop(session_id, reason)

    def release(self, camera: str, session_id: int) -> None:
        self._get(camera).release(session_id)

    def capability(self) -> dict:
        return {
            "available": bool(self._actuators),
            "protocol": "modbus-tcp",
            "configured_cameras": sorted(self._actuators),
            "write_readback": True,
            "heartbeat": True,
            "stale_ai_stop": True,
            "no_progress_stop": True,
            "max_runtime_stop": True,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for actuator in self._actuators.values():
            actuator.close()
