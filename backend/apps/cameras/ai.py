"""Клиент AI-подсчёта мешков (ai_service.py на ПК с камерами).

Модель включается per-камера по HTTP: POST /processors/cam<N> поднимает
обработчик и публикует в MediaMTX аннотированный поток cam<N>ai, DELETE
выключает его. Счётчик живёт в памяти обработчика — финальное число нужно
забирать ДО выключения.

Планшеты поста не в Tailscale и до ai_service не достают, поэтому все
вызовы идут через бэкенд, а ключ API не покидает сервер.
"""
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from numbers import Real

from .services import CAMERA_HOST

AI_URL = (
    os.environ.get("AI_SERVICE_URL")
    or os.environ.get("CAMERA_AI_URL")
    or f"http://{CAMERA_HOST}:8890"
).rstrip("/")
AI_KEY = os.environ.get("AI_SERVICE_API_KEY") or os.environ.get("CAMERA_AI_KEY", "")


def _timeout() -> float:
    """Bound the server-side request timeout even when env is malformed."""
    try:
        value = float(os.environ.get("AI_SERVICE_TIMEOUT", "10"))
    except (TypeError, ValueError):
        return 10
    return value if math.isfinite(value) and value > 0 else 10


TIMEOUT = _timeout()  # запуск модели асинхронный, долгих ответов у API нет

# Контракт AI-сервиса допускает только NVR ID cam<N>. Строгая локальная
# проверка не позволяет передать произвольный path в URL camera-PC.
CAM_RE = re.compile(r"^cam[1-9][0-9]*$")
LINE_DIRECTIONS = frozenset({"any", "up", "down", "positive", "negative"})


class AiUnavailable(Exception):
    """AI-сервис не отвечает (сеть, таймаут, ПК выключен)."""


class AiError(Exception):
    """Ответ сервиса с ошибкой (401 ключ, 409 лимит камер, 400 имя)."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def enabled() -> bool:
    return bool(AI_KEY)


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{AI_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": AI_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        return e.code, payload
    except (TimeoutError, OSError) as e:  # URLError — подкласс OSError
        raise AiUnavailable(str(e)) from e


def _call(method: str, path: str, body: dict | None = None,
          none_on_404: bool = False) -> dict | None:
    status, payload = _request(method, path, body)
    if status == 404 and none_on_404:
        return None
    if status >= 400:
        detail = (payload.get("detail") or payload.get("error")
                  or f"AI-сервис: ошибка {status}")
        raise AiError(status, detail)
    return payload


def normalize(cam: str) -> str:
    """Имя камеры к виду AI-сервиса: «2» → cam2; только cam<N>."""
    cam = str(cam).strip()
    if cam.isdigit():
        cam = f"cam{cam}"
    if not CAM_RE.fullmatch(cam):
        raise AiError(400, "Неизвестная камера")
    return cam


def camera_id(cam: str) -> str:
    """Strict public API camera id: only the literal ``cam<N>`` shape."""
    camera = str(cam)
    if not CAM_RE.fullmatch(camera):
        raise AiError(400, "Неизвестная камера")
    return camera


def validate_counting_line(payload) -> dict:
    """Validate a counting-line PUT body without weakening the AI contract."""
    if not isinstance(payload, Mapping):
        raise AiError(400, "Тело запроса должно быть объектом")

    line = payload.get("line")
    if isinstance(line, Mapping):
        names = ("x1", "y1", "x2", "y2")
        if any(name not in line for name in names):
            raise AiError(400, "Укажите координаты x1, y1, x2, y2")
        coordinates = [line[name] for name in names]
    elif (isinstance(line, Sequence)
          and not isinstance(line, (str, bytes, bytearray))
          and len(line) == 4):
        coordinates = list(line)
    else:
        raise AiError(400, "Линия должна содержать четыре координаты")

    values: list[float] = []
    for coordinate in coordinates:
        if isinstance(coordinate, bool) or not isinstance(coordinate, Real):
            raise AiError(400, "Координаты линии должны быть конечными числами от 0 до 1")
        value = float(coordinate)
        if not math.isfinite(value) or value < 0 or value > 1:
            raise AiError(400, "Координаты линии должны быть конечными числами от 0 до 1")
        values.append(value)
    if values[:2] == values[2:]:
        raise AiError(400, "Начальная и конечная точки линии не должны совпадать")

    direction = payload.get("direction")
    if direction not in LINE_DIRECTIONS:
        raise AiError(
            400,
            "direction должен быть any, up, down, positive или negative",
        )
    # Send only the documented fields. The API key is injected exclusively as
    # an HTTP header in _request and can never be forwarded from user input.
    return {"line": line, "direction": direction}


def _path(cam: str) -> str:
    return f"/processors/{normalize(cam)}"


def inventory() -> dict:
    """Живой инвентарь сети цеха: devices (nvr-channel/direct/locked) + ai."""
    return _call("GET", "/cameras") or {}


def counting_line(cam: str) -> tuple[int, dict]:
    """Raw upstream response for the public counting-line proxy."""
    return _request("GET", f"/cameras/{camera_id(cam)}/line")


def save_counting_line(cam: str, payload) -> tuple[int, dict]:
    """Validate and forward one line update exactly once."""
    return _request(
        "PUT",
        f"/cameras/{camera_id(cam)}/line",
        validate_counting_line(payload),
    )


def status(cam: str) -> dict | None:
    """Статус и живой счётчик; None — модель на камере не запущена."""
    return _call("GET", _path(cam), none_on_404=True)


def start(cam: str, options: dict | None = None) -> dict:
    """Включить модель. options — source/line/direction, дефолты ai_service."""
    return _call("POST", _path(cam), body=options or {})


def reset(cam: str) -> dict:
    """Обнулить счётчик работающей модели (новая погрузка)."""
    return _call("POST", f"{_path(cam)}/reset")


def delete(cam: str) -> dict | None:
    """Перевести уже сохранённую сессию в IDLE, не делая предварительный GET."""
    return _call("DELETE", _path(cam), none_on_404=True)


def always_on_status() -> dict:
    """Desired 24/7 cameras and their live inference-only processors."""
    return _call("GET", "/always-on") or {
        "cameras": [], "source": "sub", "processors": [],
    }


def configure_always_on(cameras: list[str], source: str = "sub") -> dict:
    """Atomically persist and apply the 24/7 camera set on the camera PC."""
    normalized = list(dict.fromkeys(normalize(camera) for camera in cameras))
    if source not in {"sub", "main"}:
        raise AiError(400, "Неизвестный источник камеры")
    return _call(
        "PUT", "/always-on", {"cameras": normalized, "source": source}
    ) or {}
