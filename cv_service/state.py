from __future__ import annotations

import json
import os
from pathlib import Path

from .settings import parse_camera


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
