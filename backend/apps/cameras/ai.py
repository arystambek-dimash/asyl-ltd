import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from numbers import Real

from django.conf import settings
from django.core.cache import cache

AI_URL = settings.AI_SERVICE_URL
AI_KEY = settings.AI_SERVICE_API_KEY
TIMEOUT = settings.AI_SERVICE_TIMEOUT
GO2RTC_API = settings.GO2RTC_API_URL
MAX_JSON_RESPONSE_BYTES = 512 * 1024
MAX_ERROR_JSON_RESPONSE_BYTES = 64 * 1024
WAGON_PLATE_TIMEOUT = 15
WAGON_PLATE_MAX_BYTES = 12 * 1024 * 1024
VEHICLE_RUNTIME_PROBE_TIMEOUT = 2.0

ALWAYS_ON_CACHE_KEY = "cameras:always-on-status:v1"
ALWAYS_ON_TTL = 5
SESSION_READY_POLL_SECONDS = 0.2
DETECTIONS_CACHE_KEY = "cameras:always-on-detections:v1"
DETECTIONS_TTL = 1

WAGON_NUMBER_CACHE_KEY = "cameras:wagon-number-status:v1"
WAGON_NUMBER_TTL = 5

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


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    timeout_seconds: float | None = None,
) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{AI_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": AI_KEY, "Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(
            req,
            timeout=TIMEOUT if timeout_seconds is None else timeout_seconds,
        )
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


def _call(
    method: str,
    path: str,
    body: dict | None = None,
    none_on_404: bool = False,
    *,
    timeout_seconds: float | None = None,
) -> dict | None:
    if timeout_seconds is None:
        # Preserve the historical call shape for normal requests and tests.
        status, payload = _request(method, path, body)
    else:
        status, payload = _request(
            method,
            path,
            body,
            timeout_seconds=timeout_seconds,
        )
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
    elif (
        isinstance(line, Sequence)
        and not isinstance(line, (str, bytes, bytearray))
        and len(line) == 4
    ):
        coordinates = list(line)
    else:
        raise AiError(400, "Линия должна содержать четыре координаты")

    values: list[float] = []
    for coordinate in coordinates:
        if isinstance(coordinate, bool) or not isinstance(coordinate, Real):
            raise AiError(
                400, "Координаты линии должны быть конечными числами от 0 до 1"
            )
        value = float(coordinate)
        if not math.isfinite(value) or value < 0 or value > 1:
            raise AiError(
                400, "Координаты линии должны быть конечными числами от 0 до 1"
            )
        values.append(value)
    if values[:2] == values[2:]:
        raise AiError(400, "Начальная и конечная точки линии не должны совпадать")

    direction = payload.get("direction")
    if direction not in LINE_DIRECTIONS:
        raise AiError(
            400,
            "direction должен быть any, up, down, positive или negative",
        )
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


def vehicle_number_info() -> dict:
    """Return the live vehicle detector/OCR capability document."""
    return (
        _call(
            "GET",
            "/vehicle-number",
            timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT,
        )
        or {}
    )


def vehicle_roi(cam: str) -> dict:
    """Return one camera's canonical vehicle-plate ROI."""
    return (
        _call(
            "GET",
            f"/cameras/{camera_id(cam)}/vehicle-roi",
            timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT,
        )
        or {}
    )


def save_vehicle_roi(cam: str, payload: dict) -> tuple[int, dict]:
    """Forward one canonical ROI update with a bounded camera-PC timeout."""
    return _request(
        "PUT",
        f"/cameras/{camera_id(cam)}/vehicle-roi",
        payload,
        timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT,
    )


def status(cam: str) -> dict | None:
    """Статус и живой счётчик; None — модель на камере не запущена."""
    return _call("GET", _path(cam), none_on_404=True)


def _order_session_ready(payload: object, *, require_zero: bool) -> bool:
    if not isinstance(payload, Mapping):
        return False
    # Older camera-PC builds omit ``mode``. Only an explicit non-session mode
    # (notably always_on) proves this is not the order counter we requested.
    if payload.get("running") is not True or payload.get("mode") not in (
        None,
        "session",
    ):
        return False
    return not (require_zero and payload.get("total") != 0)


def wait_for_order_session(
    cam: str,
    payload: dict | None,
    *,
    require_zero: bool = False,
) -> dict:
    """Wait for an asynchronously starting camera-PC order counter."""
    current = payload if isinstance(payload, dict) else {}
    deadline = time.monotonic() + TIMEOUT
    while not _order_session_ready(current, require_zero=require_zero):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = (
                "AI-счётчик не подтвердил обнуление новой сессии"
                if require_zero
                else "AI-счётчик не подтвердил запуск сессии заказа"
            )
            raise AiError(503, detail)
        time.sleep(min(SESSION_READY_POLL_SECONDS, remaining))
        live = status(cam)
        if live is not None:
            current = live
    return current


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
    return (
        _normalize_always_on(payload)
        if payload is not None
        else {
            "cameras": [],
            "source": "sub",
            "processors": [],
        }
    )


def count_events(
    cam: str,
    after_id: int,
    limit: int = 500,
) -> dict | None:
    """Read one ordered page from the camera-PC durable count journal.

    ``None`` means an explicit HTTP 404 from an older camera service.  Network
    failures and every other error remain exceptions so callers never mistake
    an uncertain event stream for permission to fall back to snapshots.
    """

    camera = normalize(cam)
    if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
        raise ValueError("after_id must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    query = urllib.parse.urlencode(
        {
            "after_id": after_id,
            "limit": limit,
            "cam": camera,
        }
    )
    return _call("GET", f"/events?{query}", none_on_404=True)


def always_on_status_cached() -> dict:
    cached = cache.get(ALWAYS_ON_CACHE_KEY)
    if isinstance(cached, Exception):
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
                # The 24/7 stream is intentionally clean. The browser needs
                # the processor's applied line to build the same model layer.
                "line": row.get("line"),
                "direction": row.get("direction"),
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


def invalidate_counting_line_caches() -> None:
    """Drop processor snapshots that may still carry the previous line."""
    cache.delete_many([ALWAYS_ON_CACHE_KEY, DETECTIONS_CACHE_KEY])


def configure_always_on(cameras: list[str], source: str = "sub") -> dict:
    """Atomically persist and apply the 24/7 camera set on the camera PC."""
    normalized = list(dict.fromkeys(normalize(camera) for camera in cameras))
    if source not in {"sub", "main"}:
        raise AiError(400, "Неизвестный источник камеры")
    try:
        # Актуальный Windows-агент использует явное имя camera_sources.
        payload = _call(
            "PUT",
            "/always-on",
            {"camera_sources": normalized, "source": source},
        )
    except AiError as exc:
        if exc.status != 422:
            raise
        payload = _call(
            "PUT",
            "/always-on",
            {"cameras": normalized, "source": source},
        )
    status = _normalize_always_on(payload)
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


def camera_frame_jpeg(stream: str) -> bytes | None:
    """Свежий кадр камеры из go2rtc. ``None`` — кадра нет, это не ошибка.

    Периодическая проверка не должна падать из-за недоступной камеры: цикл
    мониторинга просто пропустит итерацию и попробует снова.
    """
    if not GO2RTC_API:
        return None
    query = urllib.parse.urlencode({"src": stream})
    request = urllib.request.Request(
        f"{GO2RTC_API}/api/frame.jpeg?{query}", method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=WAGON_PLATE_TIMEOUT) as response:
            if response.status != 200:
                return None
            frame = response.read(WAGON_PLATE_MAX_BYTES + 1)
    except (OSError, TimeoutError, urllib.error.URLError):
        return None
    if len(frame) > WAGON_PLATE_MAX_BYTES or not frame.startswith(b"\xff\xd8\xff"):
        # Не JPEG или больше лимита сервиса — отправлять такое бессмысленно.
        return None
    return frame


def detect_wagon_plate(frame: bytes) -> dict:
    """Найти табличку вагона на кадре. OCR здесь нет — только координаты."""
    request = urllib.request.Request(
        f"{AI_URL}/wagon-number/detect",
        method="POST",
        data=frame,
        headers={"X-Api-Key": AI_KEY, "Content-Type": "image/jpeg"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=WAGON_PLATE_TIMEOUT)
    except urllib.error.HTTPError as exc:
        try:
            raise AiError(exc.code, f"AI-сервис: ошибка {exc.code}") from exc
        finally:
            exc.close()
    except (http.client.HTTPException, TimeoutError, OSError) as exc:
        raise AiUnavailable(str(exc)) from exc
    try:
        return _read_json_object(response, MAX_JSON_RESPONSE_BYTES)
    finally:
        response.close()


def accepted_plate_number(payload: Mapping) -> str:
    """Номер, которому сервис доверяет сам. Иначе — пустая строка.

    OCR отдаёт по каждой табличке флаг ``accepted``: он сводит длину, контроль
    и уверенность в одно решение. Брать номер мимо него нельзя — в учёт попал
    бы неверно прочитанный вагон, а это хуже, чем незаполненное поле.
    """
    for detection in payload.get("detections") or []:
        if not isinstance(detection, Mapping):
            continue
        ocr = detection.get("ocr")
        if not isinstance(ocr, Mapping) or ocr.get("accepted") is not True:
            continue
        number = str(ocr.get("number") or payload.get("number") or "").strip()
        if number:
            return number
    return ""


def wagon_plate_scan(stream: str) -> dict | None:
    """Табличка вагона в кадре: есть ли она и распознан ли номер.

    ``None`` — ответить нельзя (нет кадра или сервис недоступен). Это не то же
    самое, что «таблички нет»: отсутствие ответа не должно читаться как
    уехавший состав.
    """
    frame = camera_frame_jpeg(stream)
    if frame is None:
        return None
    try:
        payload = detect_wagon_plate(frame)
    except (AiUnavailable, AiError):
        return None
    detections = payload.get("detections")
    if not isinstance(detections, list):
        return None
    return {"seen": bool(detections), "number": accepted_plate_number(payload)}


def wagon_plate_seen(stream: str) -> bool | None:
    """Только факт таблички — совместимость с прежними вызовами."""
    scan = wagon_plate_scan(stream)
    return None if scan is None else scan["seen"]
