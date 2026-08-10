from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path

from .settings import parse_camera, parse_line

LINE_DIRECTIONS = frozenset({"any", "up", "down", "positive", "negative"})


def normalized_line(value) -> tuple[float, float, float, float]:
    """Accept the editor's object/list shape and return safe coordinates."""

    if isinstance(value, Mapping):
        names = ("x1", "y1", "x2", "y2")
        if set(value) != set(names):
            raise ValueError("line must contain exactly x1, y1, x2 and y2")
        values = [value[name] for name in names]
    elif (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) == 4
    ):
        values = list(value)
    else:
        raise ValueError("line must contain four normalized numbers")

    coordinates: list[float] = []
    for item in values:
        if isinstance(item, bool) or not isinstance(item, Real):
            # ValueError is part of the HTTP input contract (mapped to 400).
            raise ValueError(  # noqa: TRY004
                "line coordinates must be numbers from 0 to 1"
            )
        coordinate = float(item)
        if not math.isfinite(coordinate) or not 0 <= coordinate <= 1:
            raise ValueError("line coordinates must be numbers from 0 to 1")
        coordinates.append(0.0 if coordinate == 0 else coordinate)
    if coordinates[:2] == coordinates[2:]:
        raise ValueError("line endpoints must be different")
    return tuple(coordinates)  # type: ignore[return-value]


def line_spec(coordinates: tuple[float, float, float, float]) -> str:
    def format_coordinate(value: float) -> str:
        text = f"{value:.12f}".rstrip("0").rstrip(".")
        return text or "0"

    result = ",".join(format_coordinate(value) for value in coordinates)
    parse_line(result)
    return result


def line_payload(spec: str) -> dict[str, float]:
    x1, y1, x2, y2 = parse_line(spec)
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


class CountingLineStateStore:
    """Atomic, thread-safe owner of per-camera counting lines."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _load_unlocked(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise TypeError("invalid counting-lines state")
            raw_lines = payload.get("lines", {})
            if not isinstance(raw_lines, dict):
                raise TypeError("invalid counting-lines state")
            lines: dict[str, dict[str, str]] = {}
            for raw_camera, raw_config in raw_lines.items():
                camera = parse_camera(raw_camera)
                if not isinstance(raw_config, dict):
                    raise TypeError("invalid counting-line config")
                spec = line_spec(parse_line(raw_config.get("line")))
                direction = raw_config.get("direction")
                updated_at = raw_config.get("updated_at")
                if direction not in LINE_DIRECTIONS:
                    raise ValueError("invalid counting-line direction")
                if not isinstance(updated_at, str) or not updated_at:
                    raise ValueError("invalid counting-line timestamp")
                lines[camera] = {
                    "line": spec,
                    "direction": direction,
                    "updated_at": updated_at,
                }
            return lines
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read counting-lines state: {exc}") from exc

    def load(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return self._load_unlocked()

    def get(self, camera: str) -> dict[str, str] | None:
        return self.load().get(parse_camera(camera))

    def save(self, camera: str, value, direction: str) -> dict[str, str]:
        camera = parse_camera(camera)
        coordinates = normalized_line(value)
        if direction not in LINE_DIRECTIONS:
            raise ValueError("invalid counting-line direction")
        config = {
            "line": line_spec(coordinates),
            "direction": direction,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        with self._lock:
            lines = self._load_unlocked()
            lines[camera] = config
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            payload = {"version": 1, "lines": lines}
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except OSError as exc:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RuntimeError(f"cannot save counting-lines state: {exc}") from exc
        return dict(config)


class AlwaysOnStateStore:
    """Small, atomic camera-PC owned configuration for 24/7 counters."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> tuple[list[str], str]:
        if not self.path.exists():
            return [], "sub"
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            cameras = payload.get("cameras", [])
            source = payload.get("source", "sub")
            if not isinstance(cameras, list) or source not in {"sub", "main"}:
                raise ValueError("invalid always-on state")
            normalized = list(dict.fromkeys(parse_camera(item) for item in cameras))
            return normalized, source
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read always-on state: {exc}") from exc

    def save(self, cameras: list[str], source: str) -> None:
        if source not in {"sub", "main"}:
            raise ValueError("source must be sub or main")
        normalized = list(dict.fromkeys(parse_camera(item) for item in cameras))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        payload = {"version": 1, "cameras": normalized, "source": source}
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"cannot save always-on state: {exc}") from exc


class CameraRoleStateStore:
    """Atomic camera-PC configuration for long-running camera roles."""

    def __init__(self, path: Path):
        self.path = path

    def load_wagon_number(self) -> tuple[str | None, str]:
        if not self.path.exists():
            return None, "main"
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            camera = payload.get("wagon_number_camera")
            source = payload.get("wagon_number_source", "main")
            if camera is not None:
                camera = parse_camera(camera)
            if source not in {"sub", "main"}:
                raise ValueError("invalid wagon-number source")
            return camera, source
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read camera roles state: {exc}") from exc

    def save_wagon_number(self, camera: str | None, source: str) -> None:
        if source not in {"sub", "main"}:
            raise ValueError("source must be sub or main")
        normalized = parse_camera(camera) if camera is not None else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        payload = {
            "version": 1,
            "wagon_number_camera": normalized,
            "wagon_number_source": source,
        }
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"cannot save camera roles state: {exc}") from exc
