from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


CAMERA_RE = re.compile(r"^cam[1-9][0-9]*$")
LINE_RE = re.compile(
    r"^(0(?:\.\d+)?|1(?:\.0+)?),(0(?:\.\d+)?|1(?:\.0+)?),"
    r"(0(?:\.\d+)?|1(?:\.0+)?),(0(?:\.\d+)?|1(?:\.0+)?)$"
)


def parse_camera(value: str) -> str:
    camera = str(value).strip()
    if not CAMERA_RE.fullmatch(camera):
        raise ValueError("camera ID must match cam<N>")
    return camera


def parse_line(value: str) -> tuple[float, float, float, float]:
    text = str(value).strip()
    match = LINE_RE.fullmatch(text)
    if not match:
        raise ValueError("line must contain four normalized numbers: x1,y1,x2,y2")
    line = tuple(float(part) for part in match.groups())
    if line[0] == line[2] and line[1] == line[3]:
        raise ValueError("line endpoints must be different")
    return line  # type: ignore[return-value]


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    api_key_sha256: str
    model_path: Path
    model_device: str = "0"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8890
    mediamtx_rtsp_url: str = "rtsp://127.0.0.1:8554"
    mediamtx_api_url: str = "http://127.0.0.1:9997"
    ffmpeg_path: str = "ffmpeg"
    max_active_processors: int = 2
    inference_fps: float = 8.0
    output_fps: float = 12.0
    confidence: float = 0.35
    queue_size: int = 2
    prewarm_timeout: float = 15.0
    capture_timeout_ms: int = 3000
    default_line: str = "0,0.5,1,0.5"
    prewarm_cameras: tuple[str, ...] = ()
    prewarm_source: str = "sub"

    @classmethod
    def from_env(cls) -> "Settings":
        digest = os.getenv("AI_SERVICE_API_KEY_SHA256", "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("AI_SERVICE_API_KEY_SHA256 must be a lowercase SHA-256 digest")
        if os.getenv("AI_SERVICE_API_KEY"):
            raise ValueError("AI_SERVICE_API_KEY plaintext is forbidden on camera-PC")
        base = Path(__file__).resolve().parent
        prewarm = tuple(
            parse_camera(item.strip())
            for item in os.getenv("AI_PREWARM_CAMERAS", "").split(",")
            if item.strip()
        )
        source = os.getenv("AI_PREWARM_SOURCE", "sub").strip()
        if source not in {"sub", "main"}:
            raise ValueError("AI_PREWARM_SOURCE must be sub or main")
        default_line = os.getenv("AI_DEFAULT_LINE", "0,0.5,1,0.5").strip()
        parse_line(default_line)
        confidence = float(os.getenv("AI_CONFIDENCE", "0.35"))
        if not 0 < confidence <= 1:
            raise ValueError("AI_CONFIDENCE must be in (0, 1]")
        return cls(
            api_key_sha256=digest,
            model_path=Path(os.getenv("AI_MODEL_PATH", str(base / "models" / "best.pt"))),
            model_device=os.getenv("AI_MODEL_DEVICE", "0"),
            bind_host=os.getenv("AI_BIND_HOST", "0.0.0.0"),
            bind_port=_positive_int("AI_BIND_PORT", 8890),
            mediamtx_rtsp_url=os.getenv("AI_MEDIAMTX_RTSP_URL", "rtsp://127.0.0.1:8554").rstrip("/"),
            mediamtx_api_url=os.getenv("AI_MEDIAMTX_API_URL", "http://127.0.0.1:9997").rstrip("/"),
            ffmpeg_path=os.getenv("AI_FFMPEG_PATH", "ffmpeg"),
            max_active_processors=_positive_int("AI_MAX_ACTIVE_PROCESSORS", 2),
            inference_fps=_positive_float("AI_INFERENCE_FPS", 8.0),
            output_fps=_positive_float("AI_OUTPUT_FPS", 12.0),
            confidence=confidence,
            queue_size=min(_positive_int("AI_FRAME_QUEUE_SIZE", 2), 2),
            prewarm_timeout=_positive_float("AI_PREWARM_TIMEOUT", 15.0),
            capture_timeout_ms=_positive_int("AI_CAPTURE_TIMEOUT_MS", 3000),
            default_line=default_line,
            prewarm_cameras=prewarm,
            prewarm_source=source,
        )

    def source_stream(self, camera: str, source: str) -> str:
        parse_camera(camera)
        if source not in {"sub", "main"}:
            raise ValueError("source must be sub or main")
        suffix = "sub" if source == "sub" else ""
        return f"{camera}{suffix}"
