from __future__ import annotations

import http.client
import json
import math
import ssl
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .settings import (
    CANONICAL_CONVEYOR_CLOUD_API_URL,
    Settings,
    parse_camera,
)

CLOUD_OBSERVATION_MAX_HZ = 2.0
_SEND_INTERVAL_SECONDS = 1.0 / CLOUD_OBSERVATION_MAX_HZ
CLOUD_TERMINAL_REASONS = frozenset(
    {
        "target_reached",
        "stale_ai",
        "no_progress",
        "max_runtime",
        "processor_stopped",
        "capture_failed",
        "session_setup_failed",
        "counter_regressed",
        "manual_stop",
        "controller_fault",
        "shutdown",
    }
)


@dataclass(frozen=True)
class _Observation:
    camera: str
    session_id: int
    target_total: int
    total: int
    terminal_reason: str | None


@dataclass(frozen=True)
class _Pending:
    observation: _Observation
    sequence: int | None = None
    retry_not_before: float = float("-inf")


def _post_json(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: float,
    tls_context: ssl.SSLContext,
) -> None:
    """POST to the one allowlisted HTTPS endpoint without following redirects."""

    parsed = urlsplit(url)
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        parsed.port or 443,
        timeout=timeout,
        context=tls_context,
    )
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        response = connection.getresponse()
        # Drain only a bounded error/success body so the connection can close
        # without allowing an untrusted response to consume arbitrary memory.
        raw = response.read(4096)
        if not 200 <= response.status < 300:
            raise RuntimeError("cloud observation endpoint rejected request")
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                "cloud observation endpoint returned invalid JSON"
            ) from exc
        if not isinstance(result, dict) or not (
            result.get("accepted") is True or result.get("duplicate") is True
        ):
            raise RuntimeError("cloud observation endpoint did not acknowledge request")
    finally:
        connection.close()


class CloudConveyorObserver:
    """Coalesced, observation-only cloud callback worker.

    `observe` and `terminate` only replace one cached item per camera and wake
    the worker. Network and JSON I/O never run on the inference thread.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Callable[
            [str, str, dict[str, Any], float, ssl.SSLContext], None
        ] = _post_json,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._cameras = frozenset(settings.conveyor_cloud_cameras)
        self._url = settings.conveyor_cloud_api_url
        self._api_key = settings.conveyor_cloud_api_key
        if self._cameras and self._url != CANONICAL_CONVEYOR_CLOUD_API_URL:
            raise ValueError("cloud conveyor observer requires canonical HTTPS URL")
        if self._cameras and (
            not self._api_key
            or len(self._api_key) > 1024
            or any(not 33 <= ord(character) <= 126 for character in self._api_key)
        ):
            raise ValueError("cloud conveyor observer requires outbound API key")
        self._timeout = settings.conveyor_io_timeout_seconds
        self._transport = transport
        self._clock = monotonic
        self._tls_context = ssl.create_default_context()
        self._boot_id = str(uuid.uuid4())
        self._condition = threading.Condition(threading.RLock())
        self._pending: dict[str, _Pending] = {}
        self._bindings: dict[str, tuple[int, int]] = {}
        self._terminal_sessions: dict[str, int] = {}
        self._last_totals: dict[str, int] = {}
        self._last_sent_at: dict[str, float] = {}
        self._sequence = 0
        self._closing = False
        self._close_deadline: float | None = None
        self._last_error: str | None = None
        self._thread: threading.Thread | None = None
        if self._cameras:
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="conveyor-cloud-observation",
                daemon=True,
            )
            self._thread.start()

    def configured(self, camera: str) -> bool:
        try:
            return parse_camera(camera) in self._cameras
        except ValueError:
            return False

    def begin_session(self, camera: str, session_id: int, target_total: int) -> bool:
        if not self.configured(camera):
            return False
        with self._condition:
            if self._closing:
                return False
            binding = self._bindings.get(camera)
            if binding == (session_id, target_total):
                return self._terminal_sessions.get(camera) != session_id
            self._bindings[camera] = (session_id, target_total)
            self._terminal_sessions.pop(camera, None)
            self._last_totals[camera] = 0
            self._pending.pop(camera, None)
            self._condition.notify_all()
        return True

    def observe(
        self,
        camera: str,
        session_id: int,
        target_total: int,
        total: int,
        captured_at: float,
    ) -> bool:
        """Cache the latest controlled observation without performing I/O."""

        if not math.isfinite(captured_at) or not self.configured(camera):
            return False
        with self._condition:
            if (
                self._closing
                or self._bindings.get(camera) != (session_id, target_total)
                or self._terminal_sessions.get(camera) == session_id
            ):
                return False
            previous_total = self._last_totals.get(camera, 0)
            if total < previous_total:
                terminal_reason = "counter_regressed"
            else:
                terminal_reason = "target_reached" if total >= target_total else None
                self._last_totals[camera] = total
            if terminal_reason is not None:
                self._terminal_sessions[camera] = session_id
            self._pending[camera] = _Pending(
                _Observation(
                    camera=camera,
                    session_id=session_id,
                    target_total=target_total,
                    total=max(0, total),
                    terminal_reason=terminal_reason,
                )
            )
            self._condition.notify_all()
        return True

    def terminate(
        self,
        camera: str,
        session_id: int,
        target_total: int,
        total: int,
        reason: str,
        *,
        captured_at: float | None = None,
    ) -> bool:
        """Queue a terminal observation immediately, bypassing the rate wait."""

        if reason not in CLOUD_TERMINAL_REASONS or not self.configured(camera):
            return False
        observed_at = self._clock() if captured_at is None else captured_at
        if not math.isfinite(observed_at):
            return False
        with self._condition:
            if self._closing or self._bindings.get(camera) != (
                session_id,
                target_total,
            ):
                return False
            if self._terminal_sessions.get(camera) == session_id:
                return True
            current = self._pending.get(camera)
            if (
                current is not None
                and current.observation.session_id == session_id
                and current.observation.terminal_reason is not None
            ):
                return True
            self._terminal_sessions[camera] = session_id
            safe_total = max(self._last_totals.get(camera, 0), total)
            self._pending[camera] = _Pending(
                _Observation(
                    camera=camera,
                    session_id=session_id,
                    target_total=target_total,
                    total=max(0, safe_total),
                    terminal_reason=reason,
                )
            )
            self._condition.notify_all()
        return True

    def status(self, camera: str) -> dict:
        configured = self.configured(camera)
        with self._condition:
            binding = self._bindings.get(camera)
            terminal = (
                binding is not None
                and self._terminal_sessions.get(camera) == binding[0]
            )
            return {
                "configured": configured,
                "transport": "cloud" if configured else None,
                "direct_control": False,
                "write_readback": False,
                "feedback": None,
                "session_id": binding[0] if binding else None,
                "target_total": binding[1] if binding else None,
                "state": (
                    "terminal"
                    if terminal
                    else "observing"
                    if binding
                    else "configured"
                    if configured
                    else "unconfigured"
                ),
                "terminal": terminal,
                "online": None,
                "error": self._last_error,
            }

    def capability(self) -> dict:
        return {
            "available": bool(self._cameras),
            "transport": "cloud",
            "configured_cameras": sorted(self._cameras),
            "observation_only": True,
            "direct_control": False,
            "write_readback": False,
            "max_hz_per_camera": CLOUD_OBSERVATION_MAX_HZ,
        }

    def _next_locked(self) -> tuple[_Pending | None, float | None]:
        now = self._clock()
        ready: list[_Pending] = []
        deadlines: list[float] = []
        for pending in self._pending.values():
            item = pending.observation
            rate_due = (
                self._last_sent_at.get(item.camera, float("-inf"))
                + _SEND_INTERVAL_SECONDS
            )
            due = max(rate_due, pending.retry_not_before)
            immediate_terminal = item.terminal_reason is not None and (
                pending.sequence is None or self._closing
            )
            if immediate_terminal or now >= due:
                ready.append(pending)
            else:
                deadlines.append(due)
        if ready:
            ready.sort(
                key=lambda pending: (
                    pending.observation.terminal_reason is None,
                    pending.observation.camera,
                )
            )
            pending = ready[0]
            item = pending.observation
            self._pending.pop(item.camera, None)
            self._last_sent_at[item.camera] = now
            return pending, None
        return None, min(deadlines) if deadlines else None

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                pending, deadline = self._next_locked()
                while pending is None:
                    if self._closing and not self._pending:
                        return
                    if (
                        self._close_deadline is not None
                        and self._clock() >= self._close_deadline
                    ):
                        self._pending.clear()
                        return
                    timeout = (
                        max(0.0, deadline - self._clock())
                        if deadline is not None
                        else 0.5
                    )
                    self._condition.wait(timeout)
                    pending, deadline = self._next_locked()
                item = pending.observation
                if pending.sequence is None:
                    self._sequence += 1
                    sequence = self._sequence
                else:
                    sequence = pending.sequence
            payload = {
                "protocol_version": 1,
                "camera": item.camera,
                "session_id": item.session_id,
                "target_total": item.target_total,
                "edge_boot_id": self._boot_id,
                "seq": sequence,
                "total": item.total,
                "terminal_reason": item.terminal_reason,
            }
            try:
                self._transport(
                    self._url,
                    self._api_key,
                    payload,
                    self._timeout,
                    self._tls_context,
                )
            except Exception as exc:  # noqa: BLE001 - best-effort lease input
                # Never include exception text: transports/proxies can echo
                # request headers, including the outbound credential.
                with self._condition:
                    self._last_error = f"delivery failed ({exc.__class__.__name__})"
                    if (
                        item.terminal_reason is not None
                        and not self._closing
                        and self._bindings.get(item.camera)
                        == (item.session_id, item.target_total)
                        and self._terminal_sessions.get(item.camera) == item.session_id
                    ):
                        current = self._pending.get(item.camera)
                        if current is None:
                            self._pending[item.camera] = _Pending(
                                observation=item,
                                sequence=sequence,
                                retry_not_before=(
                                    self._clock() + _SEND_INTERVAL_SECONDS
                                ),
                            )
                            self._condition.notify_all()
            else:
                with self._condition:
                    self._last_error = None

    def close(self) -> None:
        thread = self._thread
        if thread is None:
            return
        with self._condition:
            if self._closing:
                return
            self._closing = True
            self._close_deadline = self._clock() + (
                (len(self._cameras) + 1) * self._timeout + 0.5
            )
            # Active heartbeats during shutdown could extend a server lease.
            # Manager queues terminal observations first; retain only those.
            self._pending = {
                camera: item
                for camera, item in self._pending.items()
                if item.observation.terminal_reason is not None
            }
            self._condition.notify_all()
        thread.join(timeout=(len(self._cameras) + 1) * self._timeout + 1.0)
