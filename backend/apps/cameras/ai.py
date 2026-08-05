"""Клиент AI-подсчёта мешков (ai_service.py на ПК с камерами).

Модель включается per-камера по HTTP: POST /processors/cam<N> поднимает
обработчик и публикует в MediaMTX аннотированный поток cam<N>ai, DELETE
выключает его. Счётчик живёт в памяти обработчика — финальное число нужно
забирать ДО выключения.

Планшеты поста не в Tailscale и до ai_service не достают, поэтому все
вызовы идут через бэкенд, а ключ API не покидает сервер.
"""
import http.client
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from numbers import Real

from django.core.cache import cache

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
MAX_JSON_RESPONSE_BYTES = 512 * 1024
MAX_ERROR_JSON_RESPONSE_BYTES = 64 * 1024
# Снимок 24/7 для опрашивающих экранов. Короткий TTL: цифры на экране остаются
# «живыми», но частый опрос перестаёт быть сетевым вызовом на каждый запрос.
ALWAYS_ON_CACHE_KEY = "cameras:always-on-status:v1"
ALWAYS_ON_TTL = 5
# Рамки живут отдельно от общего снимка: мешок проезжает кадр за секунды, и
# пятисекундный снимок рисовал рамку там, где мешка уже нет. Здесь TTL короче
# ровно настолько, чтобы рамка держалась мешка, а сам вызов к ПК цеха остался
# кэшированным — иначе каждый опрос снова платил бы полный TIMEOUT при
# выключенном цехе.
DETECTIONS_CACHE_KEY = "cameras:always-on-detections:v1"
DETECTIONS_TTL = 1
WAGON_NUMBER_CACHE_KEY = "cameras:wagon-number-status:v1"
WAGON_NUMBER_TTL = 5

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


def _invalid_json_response(status: int | None, detail: str):
    if status is not None:
        raise AiError(status, f"AI-сервис: ошибка {status}")
    raise AiUnavailable(detail)


def _read_json_object(response, limit: int, *, error_status: int | None = None) -> dict:
    try:
        raw = response.read(limit + 1)
    except (http.client.HTTPException, TimeoutError, OSError) as exc:
        raise AiUnavailable(str(exc)) from exc
    if not isinstance(raw, (bytes, bytearray, str)):
        _invalid_json_response(error_status, "AI-сервис вернул некорректный ответ")
    if len(raw) > limit:
        _invalid_json_response(error_status, "AI-сервис вернул слишком большой ответ")
    try:
        payload = json.loads(raw or b"{}")
    except (RecursionError, TypeError, ValueError) as exc:
        if error_status is not None:
            raise AiError(
                error_status,
                f"AI-сервис: ошибка {error_status}",
            ) from exc
        raise AiUnavailable("AI-сервис вернул некорректный ответ") from exc
    if not isinstance(payload, dict):
        _invalid_json_response(error_status, "AI-сервис вернул некорректный ответ")
    return payload


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{AI_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": AI_KEY, "Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        try:
            return e.code, _read_json_object(
                e,
                MAX_ERROR_JSON_RESPONSE_BYTES,
                error_status=e.code,
            )
        finally:
            e.close()
    except (http.client.HTTPException, TimeoutError, OSError) as e:
        # URLError — подкласс OSError.
        raise AiUnavailable(str(e)) from e
    try:
        status = response.status
        is_error = status >= 400
        payload = _read_json_object(
            response,
            MAX_ERROR_JSON_RESPONSE_BYTES if is_error else MAX_JSON_RESPONSE_BYTES,
            error_status=status if is_error else None,
        )
        return status, payload
    finally:
        response.close()


def _call(method: str, path: str, body: dict | None = None,
          none_on_404: bool = False) -> dict | None:
    status, payload = _request(method, path, body)
    if status == 404 and none_on_404:
        return None
    if status >= 400:
        detail = payload.get("detail") or payload.get("error")
        if not isinstance(detail, str) or not detail.strip():
            detail = f"AI-сервис: ошибка {status}"
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
    payload = _call("POST", _path(cam), body=options or {})
    assert payload is not None  # none_on_404 is false, so errors raise above
    return payload


def reset(cam: str) -> dict:
    """Обнулить счётчик работающей модели (новая погрузка)."""
    payload = _call("POST", f"{_path(cam)}/reset")
    assert payload is not None  # none_on_404 is false, so errors raise above
    return payload


def delete(cam: str) -> dict | None:
    """Перевести уже сохранённую сессию в IDLE, не делая предварительный GET."""
    return _call("DELETE", _path(cam), none_on_404=True)


def _normalize_always_on(payload: dict | None) -> dict:
    """Accept both generations of the Windows always-on API response."""
    result = dict(payload or {})
    if "cameras" not in result and isinstance(result.get("camera_sources"), list):
        result["cameras"] = result["camera_sources"]
    return result


def always_on_status() -> dict:
    """Desired 24/7 cameras and their live inference-only processors."""
    payload = _call("GET", "/always-on")
    return _normalize_always_on(payload) if payload is not None else {
        "cameras": [], "source": "sub", "processors": [],
    }


def always_on_status_cached() -> dict:
    """Read path for polling views: a snapshot at most ``ALWAYS_ON_TTL`` old.

    The monoblock page re-reads 24/7 state on a timer. Calling the camera PC on
    every GET means each request pays a network round trip, and a full
    ``TIMEOUT`` wait whenever the shop floor is offline — which is exactly what
    left the page stuck on "Загрузка…". Writes stay uncached and invalidate
    this snapshot, so an administrator still sees their change immediately.
    """
    cached = cache.get(ALWAYS_ON_CACHE_KEY)
    if isinstance(cached, Exception):
        # Отрицательный результат тоже кэшируется: иначе каждый опрос при
        # выключенном ПК цеха снова платит полный TIMEOUT, и подряд идущие
        # запросы копят это ожидание.
        raise cached
    if cached is not None:
        return cached
    try:
        status = always_on_status()
    except (AiUnavailable, AiError) as outage:
        cache.set(ALWAYS_ON_CACHE_KEY, outage, ALWAYS_ON_TTL)
        raise
    cache.set(ALWAYS_ON_CACHE_KEY, status, ALWAYS_ON_TTL)
    return status


def always_on_detections_cached() -> dict:
    """Только рамки процессоров — лёгкий ответ для частого опроса монитора.

    Отдаётся тем же вызовом ``/always-on``, но со своим коротким TTL: экран
    тянет рамки раз в секунду, а тяжёлые настройки и аналитика продолжают
    жить на общем пятисекундном снимке.

    Отрицательный результат кэшируется так же, как в общем снимке: при
    выключенном ПК цеха частый опрос иначе копил бы полные ``TIMEOUT``.
    """
    cached = cache.get(DETECTIONS_CACHE_KEY)
    if isinstance(cached, Exception):
        raise cached
    if cached is not None:
        return cached
    try:
        status = always_on_status()
    except (AiUnavailable, AiError) as outage:
        cache.set(DETECTIONS_CACHE_KEY, outage, DETECTIONS_TTL)
        raise
    payload = {
        "processors": [
            {
                "cam": row.get("cam"),
                "running": row.get("running"),
                "total": row.get("total"),
                "detections": row.get("detections") or [],
                "detection_frame": row.get("detection_frame"),
                "last_frame_at": row.get("last_frame_at"),
            }
            for row in status.get("processors", [])
            if isinstance(row, dict)
        ],
    }
    cache.set(DETECTIONS_CACHE_KEY, payload, DETECTIONS_TTL)
    return payload


def cached_always_on_status() -> dict | None:
    """Уже готовый снимок, либо None — никогда не ходит по сети.

    Для необязательных подсказок (например, предварительной проверки лимита
    процессоров) ожидание ``TIMEOUT`` неоправданно: без снимка просто
    пропускаем подсказку, а авторитетное решение принимает сам ПК цеха.
    """
    cached = cache.get(ALWAYS_ON_CACHE_KEY)
    return None if isinstance(cached, Exception) else cached


def invalidate_always_on_cache() -> None:
    cache.delete(ALWAYS_ON_CACHE_KEY)


def configure_always_on(cameras: list[str], source: str = "sub") -> dict:
    """Atomically persist and apply the 24/7 camera set on the camera PC."""
    normalized = list(dict.fromkeys(normalize(camera) for camera in cameras))
    if source not in {"sub", "main"}:
        raise AiError(400, "Неизвестный источник камеры")
    try:
        # Актуальный Windows-агент использует явное имя camera_sources.
        payload = _call(
            "PUT", "/always-on",
            {"camera_sources": normalized, "source": source},
        )
    except AiError as exc:
        if exc.status != 422:
            raise
        # Совместимость с предыдущим пакетом камеры на время поэтапного
        # обновления: его строгая Pydantic-схема принимала поле cameras.
        payload = _call(
            "PUT", "/always-on", {"cameras": normalized, "source": source},
        )
    status = _normalize_always_on(payload)
    # The administrator must see their own change immediately, not a snapshot
    # taken before it. Store the authoritative response as the new snapshot.
    cache.set(ALWAYS_ON_CACHE_KEY, status, ALWAYS_ON_TTL)
    return status


def wagon_number_status() -> dict:
    payload = _call("GET", "/camera-roles/wagon-number")
    return payload or {
        "camera": None,
        "source": "main",
        "stream": None,
        "assigned": False,
        "mode": "wagon_number_24_7",
    }


def wagon_number_status_cached() -> dict:
    cached = cache.get(WAGON_NUMBER_CACHE_KEY)
    if isinstance(cached, Exception):
        raise cached
    if cached is not None:
        return cached
    try:
        result = wagon_number_status()
    except (AiUnavailable, AiError) as outage:
        cache.set(WAGON_NUMBER_CACHE_KEY, outage, WAGON_NUMBER_TTL)
        raise
    cache.set(WAGON_NUMBER_CACHE_KEY, result, WAGON_NUMBER_TTL)
    return result


def configure_wagon_number(camera: str | None, source: str = "main") -> dict:
    normalized = normalize(camera) if camera is not None else None
    if source not in {"sub", "main"}:
        raise AiError(400, "Неизвестный источник камеры")
    payload = _call(
        "PUT",
        "/camera-roles/wagon-number",
        {"camera": normalized, "source": source},
    )
    result = payload or {
        "camera": normalized,
        "source": source,
        "stream": None,
        "assigned": normalized is not None,
        "mode": "wagon_number_24_7",
    }
    cache.set(WAGON_NUMBER_CACHE_KEY, result, WAGON_NUMBER_TTL)
    return result


def delete_recordings(stream: str, starts: list[str]) -> dict:
    """Delete exact recording segments on the camera PC through its secured API."""
    return _call("DELETE", "/recordings", {"stream": stream, "starts": starts}) or {}
