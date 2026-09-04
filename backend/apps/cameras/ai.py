import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from numbers import Real
from uuid import UUID

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
# Kazakhstan series may carry two or three letters: 123ABC02 and 160AL17.
VEHICLE_PLATE_RE = re.compile(r"^[0-9]{3}[A-Z]{2,3}[0-9]{2}$")
MAX_VEHICLE_CONFIRMATION_VOTES = 32_767

ALWAYS_ON_CACHE_KEY = "cameras:always-on-status:v2"
ALWAYS_ON_TTL = 5
SESSION_READY_POLL_SECONDS = 0.2
DETECTIONS_CACHE_KEY = "cameras:always-on-detections:v2"
DETECTIONS_TTL = 1

WAGON_NUMBER_CACHE_KEY = "cameras:wagon-number-status:v1"
WAGON_NUMBER_TTL = 5

CAM_RE = re.compile(r"^cam[1-9][0-9]*$")
LINE_DIRECTIONS = frozenset({"any", "up", "down", "positive", "negative"})


class AiUnavailable(Exception):
    """AI-сервис не отвечает (сеть, таймаут, ПК выключен)."""


class AiProtocolError(AiUnavailable):
    """AI service replied, but its successful response broke the contract."""


class AiError(Exception):
    """Ответ сервиса с ошибкой (401 ключ, 409 лимит камер, 400 имя)."""

    def __init__(self, status: int, detail: str, payload: Mapping | None = None):
        self.status = status
        self.detail = detail
        self.payload = dict(payload) if isinstance(payload, Mapping) else {}
        super().__init__(detail)


def enabled() -> bool:
    return bool(AI_KEY)


def _invalid_json_response(status: int | None, detail: str):
    if status is not None:
        raise AiError(status, f"AI-сервис: ошибка {status}")
    raise AiProtocolError(detail)


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
        raise AiProtocolError(
            "AI-сервис вернул некорректный ответ"
        ) from exc
    if not isinstance(payload, dict):
        _invalid_json_response(error_status, "AI-сервис вернул некорректный ответ")
    return payload


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    timeout_seconds: float | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict]:
    request_headers = {
        "X-Api-Key": AI_KEY,
        "Content-Type": "application/json",
    }
    if idempotency_key is not None:
        request_headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(
        f"{AI_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=request_headers,
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
        raise AiError(status, detail, payload)
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


def _recognize_vehicle_from_camera(
    cam: str,
    request_id: UUID | str,
    stable_weight_at: str,
    *,
    retry_only: bool,
) -> dict:
    camera = camera_id(cam)
    raw_request_id = str(request_id)
    try:
        parsed_request_id = UUID(raw_request_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("request_id must be a canonical UUID") from exc
    if str(parsed_request_id) != raw_request_id:
        raise ValueError("request_id must be a canonical UUID")
    if not isinstance(stable_weight_at, str) or not stable_weight_at:
        raise ValueError("stable_weight_at must be a timestamp")

    status, payload = _request(
        "POST",
        (
            f"/cameras/{camera}/vehicle-recognition-retry"
            if retry_only
            else f"/cameras/{camera}/vehicle-recognition"
        ),
        {"stable_weight_at": stable_weight_at},
        timeout_seconds=settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS,
        idempotency_key=raw_request_id,
    )
    if status != 200:
        detail = payload.get("error") or payload.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            detail = f"AI-сервис: ошибка {status}"
        raise AiError(status, detail, payload)

    confirmation = payload.get("confirmation")
    number = payload.get("vehicle_number")
    frames_scanned = payload.get("frames_scanned")
    configured_source = settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE
    expected_optional_metadata = {
        "request_id": raw_request_id,
        "camera": camera,
        "source": configured_source,
        "stable_weight_at": stable_weight_at,
    }
    if (
        payload.get("status") != "recognized"
        or ("ok" in payload and payload.get("ok") is not True)
        or not isinstance(number, str)
        or VEHICLE_PLATE_RE.fullmatch(number) is None
        or not isinstance(confirmation, Mapping)
        or isinstance(frames_scanned, bool)
        or not isinstance(frames_scanned, int)
        or not 1 <= frames_scanned <= 1_000_000
        or any(
            field in payload and payload.get(field) != expected
            for field, expected in expected_optional_metadata.items()
        )
        or (
            "recognized_at" in payload
            and (
                not isinstance(payload.get("recognized_at"), str)
                or not payload.get("recognized_at")
            )
        )
    ):
        raise AiProtocolError(
            "AI-сервис вернул некорректный результат номера"
        )

    votes = confirmation.get("votes")
    detector_confidence = confirmation.get("detector_confidence")
    ocr_confidence = confirmation.get("ocr_confidence")
    if (
        isinstance(votes, bool)
        or not isinstance(votes, int)
        or votes < 1
        or votes > MAX_VEHICLE_CONFIRMATION_VOTES
        or isinstance(detector_confidence, bool)
        or not isinstance(detector_confidence, Real)
        or not math.isfinite(float(detector_confidence))
        or not 0 <= float(detector_confidence) <= 1
        or isinstance(ocr_confidence, bool)
        or not isinstance(ocr_confidence, Real)
        or not math.isfinite(float(ocr_confidence))
        or not 0 <= float(ocr_confidence) <= 1
    ):
        raise AiProtocolError(
            "AI-сервис вернул некорректную уверенность OCR"
        )
    # The production endpoint intentionally returns only recognition data.
    # Bind missing audit metadata to this already validated HTTP request.  If
    # a newer Camera-PC returns those optional fields, the checks above still
    # reject any cross-request or cross-camera conflict before normalization.
    normalized = dict(payload)
    normalized.update(expected_optional_metadata)
    normalized["ok"] = True
    normalized.setdefault(
        "recognized_at",
        datetime.now(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
    )
    return normalized


def recognize_vehicle_from_camera(
    cam: str,
    request_id: UUID | str,
    stable_weight_at: str,
) -> dict:
    """Start the one allowed camera claim for a fresh scale observation.

    The camera PC owns frame selection, ROI filtering and OCR consensus. Asyl
    sends only the stable-weight timestamp and remains the authoritative owner
    of the physical weight itself.
    """

    return _recognize_vehicle_from_camera(
        cam,
        request_id,
        stable_weight_at,
        retry_only=False,
    )


def retry_vehicle_recognition_from_camera(
    cam: str,
    request_id: UUID | str,
    stable_weight_at: str,
) -> dict:
    """Replay an existing camera-PC claim without permission to create one."""

    return _recognize_vehicle_from_camera(
        cam,
        request_id,
        stable_weight_at,
        retry_only=True,
    )


VEHICLE_FRAME_TIMEOUT = 5.0
VEHICLE_FRAME_MAX_BYTES = 4 * 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"


def fetch_vehicle_recognition_frame(cam: str, request_id: UUID | str) -> bytes | None:
    """Download the evidence JPEG Camera-PC kept for one recognition request.

    ``None`` means "no photo" (unknown request, retention expired, or the
    camera PC is unreachable). Callers treat the photo as best-effort audit
    material and never let its absence change accounting.
    """

    camera = camera_id(cam)
    raw_request_id = str(request_id)
    try:
        parsed = UUID(raw_request_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("request_id must be a canonical UUID") from exc
    if str(parsed) != raw_request_id:
        raise ValueError("request_id must be a canonical UUID")

    request = urllib.request.Request(
        f"{AI_URL}/cameras/{camera}/vehicle-recognition/{raw_request_id}/frame",
        method="GET",
        headers={"X-Api-Key": AI_KEY, "Accept": "image/jpeg"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=VEHICLE_FRAME_TIMEOUT)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 404:
            return None
        raise AiError(exc.code, f"AI-сервис: ошибка {exc.code}") from exc
    except (http.client.HTTPException, TimeoutError, OSError) as exc:
        raise AiUnavailable(str(exc)) from exc
    try:
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0]
        if content_type.strip().lower() != "image/jpeg":
            raise AiProtocolError("AI-сервис вернул кадр в неожиданном формате")
        try:
            data = response.read(VEHICLE_FRAME_MAX_BYTES + 1)
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            raise AiUnavailable(str(exc)) from exc
    finally:
        response.close()
    if len(data) > VEHICLE_FRAME_MAX_BYTES:
        raise AiProtocolError("AI-сервис вернул слишком большой кадр")
    if not data.startswith(_JPEG_MAGIC):
        raise AiProtocolError("AI-сервис вернул некорректный кадр")
    return bytes(data)


def status(cam: str) -> dict | None:
    """Статус и живой счётчик; None — модель на камере не запущена."""
    return _call("GET", _path(cam), none_on_404=True)


def assert_order_session_identity(
    payload: object,
    expected_session_id: int | None,
) -> None:
    """Require the worker to prove the exact database-session identity."""

    if not isinstance(payload, Mapping) or expected_session_id is None:
        return
    worker_session_id = payload.get("session_id")
    if type(worker_session_id) is not int:
        raise AiError(
            409,
            "AI-счётчик не подтвердил точный session_id; "
            "обновите страницу",
        )
    if worker_session_id != expected_session_id:
        raise AiError(
            409,
            "AI-счётчик принадлежит другой сессии; обновите страницу",
        )


def _order_session_ready(
    payload: object,
    *,
    expected_session_id: int | None,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if payload.get("running") is not True or payload.get("mode") != "session":
        return False
    assert_order_session_identity(payload, expected_session_id)
    # Order counting may only attach to the uninterrupted shipping processor.
    # A cold or AI-24/7 session would put the same physical crossing into the
    # wrong business ledger.
    if (
        payload.get("continuous_analytics") is not True
        or payload.get("analytics_scope") != "shipping"
    ):
        raise AiError(
            409,
            "Камера ещё не готова в непрерывном контуре отгрузки; "
            "повторите запуск позже",
        )
    return True


def wait_for_order_session(
    cam: str,
    payload: dict | None,
    *,
    expected_session_id: int | None = None,
) -> dict:
    """Wait for an asynchronously starting camera-PC order counter."""
    current = payload if isinstance(payload, dict) else {}
    deadline = time.monotonic() + TIMEOUT
    while not _order_session_ready(
        current,
        expected_session_id=expected_session_id,
    ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AiError(503, "AI-счётчик не подтвердил запуск сессии заказа")
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


def reset(cam: str, session_id: int) -> dict:
    """Обнулить счётчик работающей модели (новая погрузка)."""
    payload = _call(
        "POST",
        f"{_path(cam)}/reset",
        {"session_id": session_id},
    )
    assert payload is not None  # none_on_404 is false, so errors raise above
    return payload


def delete(cam: str, session_id: int | None = None) -> dict | None:
    """Перевести уже сохранённую сессию в IDLE, не делая предварительный GET."""
    body = {"session_id": session_id} if session_id is not None else None
    return _call("DELETE", _path(cam), body=body, none_on_404=True)


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
            "analytics_scopes": {},
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
            # Version 2 is the role-aware journal. Legacy backends omit this
            # parameter and therefore see AI-24/7 rows only during rollback.
            "contract_version": 2,
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
                "bags_present": (
                    row.get("bags_present")
                    if type(row.get("bags_present")) is bool
                    else None
                ),
                "detections": row.get("detections") or [],
                "detection_frame": row.get("detection_frame"),
                # The 24/7 stream is intentionally clean. The browser needs
                # the processor's applied line to build the same model layer.
                "line": row.get("line"),
                "direction": row.get("direction"),
                "last_frame_at": row.get("last_frame_at"),
                "analytics_scope": row.get("analytics_scope"),
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
    cache.delete_many([ALWAYS_ON_CACHE_KEY, DETECTIONS_CACHE_KEY])


def invalidate_counting_line_caches() -> None:
    """Drop processor snapshots that may still carry the previous line."""
    cache.delete_many([ALWAYS_ON_CACHE_KEY, DETECTIONS_CACHE_KEY])


def configure_always_on(
    cameras: list[str],
    source: str = "sub",
    analytics_scopes: Mapping[str, str] | None = None,
) -> dict:
    """Atomically persist both continuous contours on the camera PC."""
    normalized = list(dict.fromkeys(normalize(camera) for camera in cameras))
    if source not in {"sub", "main"}:
        raise AiError(400, "Неизвестный источник камеры")
    if not isinstance(analytics_scopes, Mapping):
        raise AiError(400, "Не указаны роли непрерывных камер")
    normalized_scopes: dict[str, str] = {}
    for raw_camera, scope in analytics_scopes.items():
        if not isinstance(raw_camera, str):
            raise AiError(400, "Некорректная роль камеры")
        camera = normalize(raw_camera)
        if scope not in {"shipping", "ai_247"}:
            raise AiError(400, "Некорректная роль камеры")
        normalized_scopes[camera] = scope
    if set(normalized_scopes) != set(normalized):
        raise AiError(400, "Роли камер не совпадают со списком процессоров")
    # Role-aware agents are mandatory. Falling back to the old `cameras`
    # contract would silently merge shipping into AI 24/7 analytics.
    payload = _call(
        "PUT",
        "/always-on",
        {
            "camera_sources": normalized,
            "source": source,
            "analytics_scopes": normalized_scopes,
        },
    )
    status = _normalize_always_on(payload)
    cache.set(ALWAYS_ON_CACHE_KEY, status, ALWAYS_ON_TTL)
    cache.delete(DETECTIONS_CACHE_KEY)
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
