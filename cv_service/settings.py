from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CAMERA_RE = re.compile(r"^cam[1-9][0-9]*$")
MAX_CONVEYOR_HEARTBEAT_SECONDS = 0.5
MAX_CONVEYOR_STALE_AI_SECONDS = 1.5
MAX_CONVEYOR_IO_TIMEOUT_SECONDS = 0.5
MAX_CONVEYOR_COMMAND_TIMEOUT_SECONDS = 5.0
MAX_CONVEYOR_NO_PROGRESS_SECONDS = 30.0
MAX_CONVEYOR_MAX_RUN_SECONDS = 900.0
CANONICAL_CONVEYOR_CLOUD_API_URL = (
    "https://asyl-ltd.kz/api/conveyors/v1/ai/observation/"
)
LINE_RE = re.compile(
    r"^(0(?:\.\d+)?|1(?:\.0+)?),(0(?:\.\d+)?|1(?:\.0+)?),"
    r"(0(?:\.\d+)?|1(?:\.0+)?),(0(?:\.\d+)?|1(?:\.0+)?)$"
)


@dataclass(frozen=True)
class ConveyorControllerSettings:
    host: str
    port: int
    unit_id: int
    register_type: str
    address: int
    on_value: int
    off_value: int
    feedback_register_type: str
    feedback_address: int
    feedback_on_value: int
    feedback_off_value: int


def _json_int(value, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _register_values(
    register_type: str, on_value, off_value, *, prefix: str,
) -> tuple[int, int]:
    if register_type not in {"coil", "holding"}:
        raise ValueError(f"{prefix}register_type must be coil or holding")
    maximum = 1 if register_type == "coil" else 65535
    on = _json_int(on_value, name=f"{prefix}on_value", minimum=0, maximum=maximum)
    off = _json_int(off_value, name=f"{prefix}off_value", minimum=0, maximum=maximum)
    if on == off:
        raise ValueError(f"{prefix}on_value and {prefix}off_value must differ")
    return on, off


def parse_conveyor_controllers(value: str) -> dict[str, ConveyorControllerSettings]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("AI_CONVEYOR_CONTROLLERS_JSON must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("AI_CONVEYOR_CONTROLLERS_JSON root must be an object")

    controllers: dict[str, ConveyorControllerSettings] = {}
    # Modbus address spaces are scoped by endpoint, unit and register table.
    # Every command and feedback point is exclusive: sharing feedback can make
    # one camera "prove" another belt's state, while a command/feedback overlap
    # can turn a readback into an actuator write target.
    physical_points: dict[
        tuple[str, int, int, str, int], tuple[str, str]
    ] = {}
    allowed = {
        "host", "port", "unit_id", "register_type", "address",
        "on_value", "off_value", "feedback_register_type",
        "feedback_address", "feedback_on_value", "feedback_off_value",
    }
    for raw_camera, raw in payload.items():
        camera = parse_camera(raw_camera)
        if not isinstance(raw, dict):
            raise ValueError(f"conveyor controller for {camera} must be an object")
        unexpected = sorted(set(raw) - allowed)
        if unexpected:
            raise ValueError(
                f"unknown conveyor controller fields for {camera}: {', '.join(unexpected)}"
            )
        host = raw.get("host")
        if (
            not isinstance(host, str)
            or not host.strip()
            or len(host.strip()) > 253
            or any(item in host for item in ("/", "\\", "@"))
        ):
            raise ValueError(f"conveyor host for {camera} is invalid")
        host = host.strip()
        port = _json_int(raw.get("port", 502), name="port", minimum=1, maximum=65535)
        unit_id = _json_int(
            raw.get("unit_id", 1), name="unit_id", minimum=0, maximum=247,
        )
        register_type = raw.get("register_type", "coil")
        if not isinstance(register_type, str):
            raise ValueError("register_type must be coil or holding")
        register_type = register_type.strip().lower()
        address = _json_int(
            raw.get("address"), name="address", minimum=0, maximum=65535,
        )
        on_value, off_value = _register_values(
            register_type,
            raw.get("on_value", 1),
            raw.get("off_value", 0),
            prefix="",
        )
        feedback_type = raw.get("feedback_register_type", register_type)
        if not isinstance(feedback_type, str):
            raise ValueError("feedback_register_type must be coil or holding")
        feedback_type = feedback_type.strip().lower()
        if "feedback_address" not in raw:
            raise ValueError(
                f"feedback_address for {camera} is required; command echo is not physical feedback"
            )
        feedback_address = _json_int(
            raw.get("feedback_address"),
            name="feedback_address",
            minimum=0,
            maximum=65535,
        )
        if feedback_type == register_type and feedback_address == address:
            raise ValueError(
                f"feedback for {camera} must use a separate input/register"
            )
        feedback_on, feedback_off = _register_values(
            feedback_type,
            raw.get("feedback_on_value", on_value),
            raw.get("feedback_off_value", off_value),
            prefix="feedback_",
        )
        command_point = (
            host.casefold(), port, unit_id, register_type, address,
        )
        feedback_point = (
            host.casefold(), port, unit_id, feedback_type, feedback_address,
        )
        for role, point in (
            ("command", command_point),
            ("feedback", feedback_point),
        ):
            assigned = physical_points.get(point)
            if assigned is not None:
                assigned_camera, assigned_role = assigned
                raise ValueError(
                    f"conveyor {role} point for {camera} conflicts with "
                    f"{assigned_role} point for {assigned_camera}; one physical "
                    "Modbus point cannot be assigned to multiple cameras"
                )
            physical_points[point] = (camera, role)
        controllers[camera] = ConveyorControllerSettings(
            host=host,
            port=port,
            unit_id=unit_id,
            register_type=register_type,
            address=address,
            on_value=on_value,
            off_value=off_value,
            feedback_register_type=feedback_type,
            feedback_address=feedback_address,
            feedback_on_value=feedback_on,
            feedback_off_value=feedback_off,
        )
    return controllers


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


def _positive_float(
    name: str,
    default: float,
    *,
    maximum: float | None = None,
) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum:g}")
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
    always_on_state_path: Path = Path("data/always-on.json")
    camera_roles_state_path: Path = Path("data/camera-roles.json")
    counting_lines_state_path: Path = Path("data/counting-lines.json")
    wagon_frame_max_bytes: int = 12 * 1024 * 1024
    conveyor_controllers: dict[str, ConveyorControllerSettings] = field(default_factory=dict)
    conveyor_cloud_cameras: tuple[str, ...] = ()
    conveyor_cloud_api_url: str = CANONICAL_CONVEYOR_CLOUD_API_URL
    conveyor_cloud_api_key: str = field(default="", repr=False)
    conveyor_heartbeat_seconds: float = 0.5
    conveyor_stale_ai_seconds: float = 1.5
    conveyor_no_progress_seconds: float = 15.0
    conveyor_max_run_seconds: float = 300.0
    conveyor_io_timeout_seconds: float = 0.5
    conveyor_command_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        overlap = sorted(
            set(self.conveyor_cloud_cameras) & set(self.conveyor_controllers)
        )
        if overlap:
            raise ValueError(
                "cloud and direct conveyor camera mappings overlap: "
                + ", ".join(overlap)
            )
        if self.conveyor_cloud_cameras:
            if self.conveyor_cloud_api_url != CANONICAL_CONVEYOR_CLOUD_API_URL:
                raise ValueError(
                    "AI_CONVEYOR_CLOUD_API_URL must be the canonical HTTPS endpoint"
                )
            key = self.conveyor_cloud_api_key
            if (
                not key
                or key != key.strip()
                or len(key) > 1024
                or any(not 33 <= ord(character) <= 126 for character in key)
            ):
                raise ValueError("AI_CONVEYOR_CLOUD_API_KEY is invalid")

    @classmethod
    def from_env(cls) -> Settings:
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
        heartbeat = _positive_float(
            "AI_CONVEYOR_HEARTBEAT_SECONDS",
            0.5,
            maximum=MAX_CONVEYOR_HEARTBEAT_SECONDS,
        )
        stale_ai = _positive_float(
            "AI_CONVEYOR_STALE_AI_SECONDS",
            1.5,
            maximum=MAX_CONVEYOR_STALE_AI_SECONDS,
        )
        if stale_ai < heartbeat * 2:
            raise ValueError(
                "AI_CONVEYOR_STALE_AI_SECONDS must be at least two heartbeat intervals"
            )
        no_progress = _positive_float(
            "AI_CONVEYOR_NO_PROGRESS_SECONDS",
            15.0,
            maximum=MAX_CONVEYOR_NO_PROGRESS_SECONDS,
        )
        max_run = _positive_float(
            "AI_CONVEYOR_MAX_RUN_SECONDS",
            300.0,
            maximum=MAX_CONVEYOR_MAX_RUN_SECONDS,
        )
        controllers = parse_conveyor_controllers(
            os.getenv("AI_CONVEYOR_CONTROLLERS_JSON", ""),
        )
        cloud_cameras = tuple(dict.fromkeys(
            parse_camera(item.strip())
            for item in os.getenv("AI_CONVEYOR_CLOUD_CAMERAS", "").split(",")
            if item.strip()
        ))
        cloud_url = os.getenv(
            "AI_CONVEYOR_CLOUD_API_URL",
            CANONICAL_CONVEYOR_CLOUD_API_URL,
        ).strip()
        cloud_key = os.getenv("AI_CONVEYOR_CLOUD_API_KEY", "")
        overlap = sorted(set(cloud_cameras) & set(controllers))
        if overlap:
            raise ValueError(
                "cloud and direct conveyor camera mappings overlap: "
                + ", ".join(overlap)
            )
        if cloud_cameras:
            if cloud_url != CANONICAL_CONVEYOR_CLOUD_API_URL:
                raise ValueError(
                    "AI_CONVEYOR_CLOUD_API_URL must be the canonical HTTPS endpoint"
                )
            if (
                not cloud_key
                or cloud_key != cloud_key.strip()
                or len(cloud_key) > 1024
                or any(
                    not 33 <= ord(character) <= 126
                    for character in cloud_key
                )
            ):
                raise ValueError("AI_CONVEYOR_CLOUD_API_KEY is invalid")
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
            always_on_state_path=Path(
                os.getenv(
                    "AI_ALWAYS_ON_STATE_PATH",
                    str(base / "data" / "always-on.json"),
                )
            ),
            camera_roles_state_path=Path(
                os.getenv(
                    "AI_CAMERA_ROLES_STATE_PATH",
                    str(base / "data" / "camera-roles.json"),
                )
            ),
            counting_lines_state_path=Path(
                os.getenv(
                    "AI_COUNTING_LINES_STATE_PATH",
                    str(base / "data" / "counting-lines.json"),
                )
            ),
            wagon_frame_max_bytes=_positive_int(
                "AI_WAGON_FRAME_MAX_BYTES", 12 * 1024 * 1024,
            ),
            conveyor_controllers=controllers,
            conveyor_cloud_cameras=cloud_cameras,
            conveyor_cloud_api_url=cloud_url,
            conveyor_cloud_api_key=cloud_key,
            conveyor_heartbeat_seconds=heartbeat,
            conveyor_stale_ai_seconds=stale_ai,
            conveyor_no_progress_seconds=no_progress,
            conveyor_max_run_seconds=max_run,
            conveyor_io_timeout_seconds=_positive_float(
                "AI_CONVEYOR_IO_TIMEOUT_SECONDS",
                0.5,
                maximum=MAX_CONVEYOR_IO_TIMEOUT_SECONDS,
            ),
            conveyor_command_timeout_seconds=_positive_float(
                "AI_CONVEYOR_COMMAND_TIMEOUT_SECONDS",
                3.0,
                maximum=MAX_CONVEYOR_COMMAND_TIMEOUT_SECONDS,
            ),
        )

    def source_stream(self, camera: str, source: str) -> str:
        parse_camera(camera)
        if source not in {"sub", "main"}:
            raise ValueError("source must be sub or main")
        suffix = "sub" if source == "sub" else ""
        return f"{camera}{suffix}"
