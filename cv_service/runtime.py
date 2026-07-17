from __future__ import annotations

import json
import re
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .contracts import Detection
from .settings import Settings, parse_camera


def validate_classes(classes: list[str]) -> None:
    if not classes or any(not item.strip() for item in classes):
        raise RuntimeError("Checkpoint contains no usable classes")
    invalid = [item for item in classes if not re.fullmatch(r".+_[1-9][0-9]*", item)]
    if invalid:
        raise RuntimeError(
            "Checkpoint classes must be color/weight labels such as Red_50: "
            + ", ".join(invalid)
        )


class ModelRuntime:
    """One process-wide Ultralytics model; predict is serialized by Manager."""

    instances = 0

    def __init__(self, model_path: Path, device: str, confidence: float):
        if not model_path.is_file():
            raise RuntimeError(f"AI checkpoint not found: {model_path}")
        try:
            from ultralytics import YOLO
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install production CV dependencies before starting ai_service") from exc
        ModelRuntime.instances += 1
        if ModelRuntime.instances != 1:
            raise RuntimeError("Only one model instance is allowed")
        self._np = np
        self._model = YOLO(str(model_path))
        self.path = model_path
        self.device = device
        self.confidence = confidence
        self._lock = threading.Lock()
        names = self._model.names
        if isinstance(names, dict):
            self.classes = [str(names[key]) for key in sorted(names)]
        else:
            self.classes = [str(value) for value in names]
        validate_classes(self.classes)
        # Warm-up is completed before uvicorn is started.
        sample = np.zeros((640, 640, 3), dtype=np.uint8)
        self.predict(sample)

    def predict(self, frame: Any) -> list[Detection]:
        with self._lock:
            result = self._model.predict(
                source=frame,
                device=self.device,
                conf=self.confidence,
                verbose=False,
            )[0]
        detections: list[Detection] = []
        if result.boxes is None:
            return detections
        names = result.names
        for xyxy, confidence, class_id in zip(
            result.boxes.xyxy.cpu().tolist(),
            result.boxes.conf.cpu().tolist(),
            result.boxes.cls.cpu().tolist(),
        ):
            label = str(names[int(class_id)])
            detections.append(Detection(*map(float, xyxy), float(confidence), label))
        return detections

    def metadata(self) -> dict:
        return {
            "id": self.path.name,
            "device": self.device,
            "classes": self.classes,
        }


class MediaMtxClient:
    def __init__(self, api_url: str, timeout: float = 2.0):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def paths(self) -> dict[str, dict]:
        request = urllib.request.Request(f"{self.api_url}/v3/paths/list", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read() or b"{}")
        except (OSError, urllib.error.HTTPError, ValueError) as exc:
            raise RuntimeError(f"MediaMTX API unavailable: {exc}") from exc
        items = body.get("items", [])
        return {
            str(item.get("name")): item
            for item in items
            if isinstance(item, dict) and item.get("name")
        }

    def camera_inventory(self) -> dict[str, dict]:
        paths = self.paths()
        cameras: dict[str, dict] = {}
        for name, payload in paths.items():
            candidate = name.removesuffix("sub")
            try:
                camera = parse_camera(candidate)
            except ValueError:
                continue
            if name.endswith("ai"):
                continue
            row = cameras.setdefault(camera, {"cam": camera, "main": False, "sub": False})
            source = "sub" if name.endswith("sub") else "main"
            row[source] = True
            row[f"{source}_ready"] = bool(payload.get("ready"))
        return cameras

    def device_inventory(self) -> list[dict]:
        """Compatibility inventory consumed by the Django camera wall."""
        paths = self.paths()
        devices: list[dict] = []
        for name in sorted(paths):
            if name.endswith("ai") or name.endswith("sub"):
                continue
            payload = paths[name]
            match = re.fullmatch(r"cam([1-9][0-9]*)", name)
            if match:
                channel = int(match.group(1))
                sub = f"{name}sub"
                devices.append({
                    "kind": "nvr-channel",
                    "path": name,
                    "sub": sub if sub in paths else name,
                    "channel": channel,
                    "model": f"Камера {channel}",
                    "online": bool(payload.get("ready")),
                })
                continue
            if re.fullmatch(r"cam_[A-Za-z0-9]{4,32}", name):
                sub = f"{name}sub"
                devices.append({
                    "kind": "direct",
                    "path": name,
                    "sub": sub if sub in paths else name,
                    "model": name,
                    "online": bool(payload.get("ready")),
                })
        return devices

    def validate_source(self, camera: str, source_stream: str) -> None:
        paths = self.paths()
        if source_stream not in paths:
            raise ValueError(f"MediaMTX source does not exist for {camera}: {source_stream}")

    def path_ready(self, stream: str) -> bool:
        payload = self.paths().get(stream)
        return bool(payload and payload.get("ready"))


def select_h264_encoder(ffmpeg_path: str) -> str:
    try:
        probe = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"FFmpeg encoder probe failed: {exc}") from exc
    text = f"{probe.stdout}\n{probe.stderr}"
    # Listing an encoder does not prove the GPU/driver can initialize it.
    # Probe one synthetic frame and choose the first encoder that really works.
    for encoder in ("h264_nvenc", "h264_qsv", "libx264"):
        if encoder not in text:
            continue
        try:
            subprocess.run(
                [
                    ffmpeg_path, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=size=64x64:rate=1",
                    "-frames:v", "1", "-c:v", encoder, "-f", "null", "-",
                ],
                capture_output=True,
                timeout=15,
                check=True,
            )
            return encoder
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("FFmpeg has no supported H.264 encoder")


def build_runtime(settings: Settings) -> tuple[ModelRuntime, MediaMtxClient, str]:
    model = ModelRuntime(settings.model_path, settings.model_device, settings.confidence)
    mediamtx = MediaMtxClient(settings.mediamtx_api_url)
    encoder = select_h264_encoder(settings.ffmpeg_path)
    return model, mediamtx, encoder
