import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from numbers import Real
from uuid import UUID

from django.conf import settings
from django.core.cache import cache

from apps.common.wagon_numbers import is_wagon_number, wagon_check_digit_ok

AI_URL = settings.AI_SERVICE_URL
AI_KEY = settings.AI_SERVICE_API_KEY
TIMEOUT = settings.AI_SERVICE_TIMEOUT
GO2RTC_API = settings.GO2RTC_API_URL
MAX_JSON_RESPONSE_BYTES = 512 * 1024
MAX_ERROR_JSON_RESPONSE_BYTES = 64 * 1024
# A full /events page: with bag verification on, every event carries one
# evidence sample per crossed line (~4.4 KB at eight lines, ~2.2 MB a page).
# A page that cannot be read would be requested again forever.
EVENT_PAGE_MAX_BYTES = 8 * 1024 * 1024
WAGON_PLATE_TIMEOUT = 15
WAGON_PLATE_MAX_BYTES = 12 * 1024 * 1024
VEHICLE_RUNTIME_PROBE_TIMEOUT = 2.0
# Kazakhstan plates: 123ABC02, the two-letter series 160AL17, and the 1993
# standard still carried by old trucks: X209LAN.
VEHICLE_PLATE_RE = re.compile(r"^(?:[0-9]{3}[A-Z]{2,3}[0-9]{2}|[A-Z][0-9]{3}[A-Z]{3})$")


def kz_vehicle_plate(compact: str) -> str:
    """Казахстанский номер без отметки страны «KZ» или "", если формат не тот.

    ``compact`` — уже слитная запись заглавными; символы OCR не заменяются.
    Номер известного формата с «KZ» начинаться не может, поэтому префикс
    снимается безусловно.
    """
    plate = compact.removeprefix("KZ")
    return plate if VEHICLE_PLATE_RE.fullmatch(plate) else ""


VEHICLE_ORIENTATIONS = frozenset({"front", "rear"})


def unit_interval(value: object) -> float | None:
    """Конечное число от 0 до 1 (уверенность, координата) как float, иначе None.

    ``True``/``False`` числом не считаются, хотя в Python это int.
    """
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def vehicle_orientation(payload: Mapping | None) -> tuple[str, float | None]:
    """Read the optional Camera-PC front/rear verdict as ``(label, confidence)``.

    The classifier is best effort on the camera side, so a missing, undecided
    or malformed ``orientation`` simply yields an empty label: it must never
    turn a good plate answer into an error.
    """

    if not isinstance(payload, Mapping):
        return "", None
    orientation = payload.get("orientation")
    if not isinstance(orientation, Mapping):
        return "", None
    confidence = unit_interval(orientation.get("confidence"))
    label = orientation.get("label")
    if not isinstance(label, str) or label not in VEHICLE_ORIENTATIONS:
        return "", confidence
    return label, confidence


MAX_VEHICLE_CONFIRMATION_VOTES = 32_767

ALWAYS_ON_CACHE_KEY = "cameras:always-on-status:v2"
ALWAYS_ON_TTL = 5
SESSION_READY_POLL_SECONDS = 0.2
DETECTIONS_CACHE_KEY = "cameras:always-on-detections:v2"
DETECTIONS_TTL = 1

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
    raw_body: bytes | None = None,
    content_type: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
    max_response_bytes: int = MAX_JSON_RESPONSE_BYTES,
) -> tuple[int, dict]:
    request_headers = {
        "X-Api-Key": AI_KEY,
        "Content-Type": content_type or "application/json",
    }
    if idempotency_key is not None:
        request_headers["Idempotency-Key"] = idempotency_key
    if extra_headers:
        request_headers.update(extra_headers)
    if raw_body is not None:
        data = bytes(raw_body)
    else:
        data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{AI_URL}{path}",
        method=method,
        data=data,
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
            MAX_ERROR_JSON_RESPONSE_BYTES if is_error else max_response_bytes,
            error_status=status if is_error else None,
        )
        return status, payload
    finally:
        response.close()


def _error_from_payload(status: int, payload: Mapping) -> AiError:
    """Ошибка ПК цеха: текст берём из ответа (cv-service пишет ``error``), иначе по коду."""
    detail = payload.get("error") or payload.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        detail = f"AI-сервис: ошибка {status}"
    return AiError(status, detail, payload)


def _checked(status: int, payload: dict) -> dict:
    if status >= 400:
        raise _error_from_payload(status, payload)
    return payload


def _call(method: str, path: str, body: dict | None = None, **options) -> dict:
    """JSON-запрос к ПК цеха; ответ 4xx/5xx — ``AiError``.

    ``options`` (timeout_seconds, max_response_bytes) передаются в _request
    как есть: обычный вызов сохраняет прежнюю форму для тестов.
    """
    return _checked(*_request(method, path, body, **options))


def _call_optional(
    method: str, path: str, body: dict | None = None, **options
) -> dict | None:
    """Как _call, но явный 404 — ``None`` (модель на камере не запущена)."""
    status, payload = _request(method, path, body, **options)
    return None if status == 404 else _checked(status, payload)


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


LINE_COORDINATES = ("x1", "y1", "x2", "y2")
# Mirrors cv-service ``normalize_verification_lines`` so the operator gets a
# readable Russian 400 from CRM instead of the camera PC's English one.
MAX_VERIFICATION_LINES = 8
VERIFICATION_LINE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
VERIFICATION_LINE_KEYS = frozenset({"id", "name", "line", "line_spec"})
VERIFICATION_LINE_NAME_MAX = 80
SAVED_NOT_APPLIED_DETAIL = "Сохранено, но не применено к камере — обновите статус"


def _line_coordinates(line) -> list[float]:
    """Four finite normalized coordinates of a non-degenerate segment."""
    if isinstance(line, Mapping):
        if any(name not in line for name in LINE_COORDINATES):
            raise AiError(400, "Укажите координаты x1, y1, x2, y2")
        coordinates = [line[name] for name in LINE_COORDINATES]
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
        value = unit_interval(coordinate)
        if value is None:
            raise AiError(
                400, "Координаты линии должны быть конечными числами от 0 до 1"
            )
        values.append(value)
    if values[:2] == values[2:]:
        raise AiError(400, "Начальная и конечная точки линии не должны совпадать")
    return values


def _segment_key(values: list[float]) -> tuple[float, ...]:
    # The camera PC compares segments after ``format(value, ".8g")``.
    return tuple(float(format(value, ".8g")) for value in values)


def _verification_line(item, *, ids: set[str], segments: set) -> dict:
    if not isinstance(item, Mapping) or set(item) - VERIFICATION_LINE_KEYS:
        raise AiError(
            400, "Линия проверки должна содержать id, line и необязательное name"
        )
    identifier = item.get("id")
    if not isinstance(identifier, str) or not VERIFICATION_LINE_ID_RE.fullmatch(
        identifier
    ):
        raise AiError(
            400,
            "ID линии проверки: от 1 до 40 латинских букв, цифр, «_» или «-»",
        )
    if identifier == "count" or identifier in ids:
        raise AiError(
            400,
            "ID линий проверки должны быть уникальными; «count» зарезервирован",
        )
    name = item.get("name", identifier)
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > VERIFICATION_LINE_NAME_MAX
    ):
        raise AiError(400, "Название линии проверки: от 1 до 80 символов")
    name = name.strip()

    raw_line = item["line"] if "line" in item else item.get("line_spec")
    try:
        if isinstance(raw_line, str):
            try:
                raw_line = [float(part) for part in raw_line.split(",")]
            except ValueError as exc:
                raise AiError(400, "Линия должна содержать четыре координаты") from exc
        values = _line_coordinates(raw_line)
    except AiError as exc:
        detail = exc.detail[:1].lower() + exc.detail[1:]
        raise AiError(400, f"Линия проверки «{name}»: {detail}") from exc

    key = _segment_key(values)
    if key in segments or key[2:] + key[:2] in segments:
        raise AiError(
            400,
            f"Линия проверки «{name}» совпадает с линией подсчёта "
            "или другой линией проверки",
        )
    ids.add(identifier)
    segments.add(key)
    return {"id": identifier, "name": name, "line": dict(zip(LINE_COORDINATES, values))}


def validate_verification_lines(value, *, count_line: list[float]) -> list[dict]:
    """Validate sampling lines exactly like the camera PC; ``[]`` disables them."""
    if not isinstance(value, list) or len(value) > MAX_VERIFICATION_LINES:
        raise AiError(
            400,
            "Линии проверки: передайте список не более чем из "
            f"{MAX_VERIFICATION_LINES} линий ([] — выключить проверку)",
        )
    ids: set[str] = set()
    segments = {_segment_key(count_line)}
    return [_verification_line(item, ids=ids, segments=segments) for item in value]


def validate_counting_line(payload) -> dict:
    """Validate a counting-line PUT body without weakening the AI contract.

    ``verification_lines`` is forwarded only when the client sent it: an
    absent field keeps the camera PC's lines, ``[]`` switches them off.
    """
    if not isinstance(payload, Mapping):
        raise AiError(400, "Тело запроса должно быть объектом")

    line = payload.get("line")
    count_line = _line_coordinates(line)

    direction = payload.get("direction")
    if direction not in LINE_DIRECTIONS:
        raise AiError(
            400,
            "direction должен быть any, up, down, positive или negative",
        )
    body = {"line": line, "direction": direction}
    if "verification_lines" in payload:
        body["verification_lines"] = validate_verification_lines(
            payload["verification_lines"], count_line=count_line
        )
    return body


def line_config_payload(status: int, payload: dict) -> dict:
    """Public shape of a line GET/PUT reply, stable across camera-PC versions.

    An older camera PC neither stores nor returns ``verification_lines``; the
    editor needs to know that instead of silently dropping the operator's
    lines. A 503 with ``saved: true`` is a partial success, not a failure.
    """
    if status >= 400 and payload.get("saved") is not True:
        return payload
    lines = payload.get("verification_lines")
    supported = isinstance(lines, list)
    result = {
        **payload,
        "verification_lines": lines if supported else [],
        "verification_lines_supported": supported,
    }
    if status >= 400:
        result.update(code="saved_not_applied", detail=SAVED_NOT_APPLIED_DETAIL)
    return result


def _comparable_line(value) -> tuple[float, ...] | None:
    """A line object or ``line_spec`` string as the camera PC compares it."""
    if isinstance(value, str):
        try:
            value = [float(part) for part in value.split(",")]
        except ValueError:
            return None
    try:
        return _segment_key(_line_coordinates(value))
    except AiError:
        return None


def _comparable_checks(lines) -> list[tuple] | None:
    if not isinstance(lines, list) or not all(isinstance(item, Mapping) for item in lines):
        return None
    return [
        (
            item.get("id"),
            item.get("name"),
            _comparable_line(item.get("line_spec", item.get("line"))),
        )
        for item in lines
    ]


def counting_line_application(cam: str, saved: Mapping) -> str:
    """Whether the running processor already uses the saved line settings.

    The line file only says what was saved; the processor status says what
    counts right now. ``not_running``: there is nothing to confirm — the
    camera PC loads the saved file when the model starts.
    """
    processor = status(cam)
    if not isinstance(processor, Mapping) or processor.get("processor_alive") is False:
        return "not_running"
    saved_line = _comparable_line(saved.get("line_spec", saved.get("line")))
    if (
        saved_line is None
        or _comparable_line(processor.get("line")) != saved_line
        or processor.get("direction") != saved.get("direction")
    ):
        return "not_applied"
    verification = processor.get("verification")
    # An older processor reports no verification block: judge the main line.
    if isinstance(verification, Mapping) and isinstance(saved.get("verification_lines"), list):
        running = _comparable_checks(verification.get("lines"))
        if running != _comparable_checks(saved["verification_lines"]):
            return "not_applied"
    return "applied"


def _path(cam: str) -> str:
    return f"/processors/{normalize(cam)}"


def inventory() -> dict:
    """Живой инвентарь сети цеха: devices (nvr-channel/direct/locked) + ai."""
    return _call("GET", "/cameras")


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


def _probe_get(path: str) -> dict:
    """Короткий GET настроек ПК цеха: экран не ждёт полный ``TIMEOUT``."""
    return _call("GET", path, timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT)


def _probe_put(path: str, payload: dict) -> tuple[int, dict]:
    """Проксировать сохранение настройки с тем же коротким таймаутом."""
    return _request("PUT", path, payload, timeout_seconds=VEHICLE_RUNTIME_PROBE_TIMEOUT)


def vehicle_number_info() -> dict:
    """Return the live vehicle detector/OCR capability document."""
    return _probe_get("/vehicle-number")


def vehicle_roi(cam: str) -> dict:
    """Return one camera's canonical vehicle-plate ROI."""
    return _probe_get(f"/cameras/{camera_id(cam)}/vehicle-roi")


def save_vehicle_roi(cam: str, payload: dict) -> tuple[int, dict]:
    """Forward one canonical ROI update with a bounded camera-PC timeout."""
    return _probe_put(f"/cameras/{camera_id(cam)}/vehicle-roi", payload)


def arch_motion(cam: str) -> dict:
    """Состояние зоны арки вагонных весов: едет / стоит и сколько секунд стоит."""
    return _probe_get(f"/cameras/{camera_id(cam)}/arch-motion")


def arch_zone(cam: str) -> dict:
    """Return one camera's canonical wagon-arch motion-detection zone."""
    return _probe_get(f"/cameras/{camera_id(cam)}/arch-zone")


def save_arch_zone(cam: str, payload: dict) -> tuple[int, dict]:
    """Forward one arch-zone update with a bounded camera-PC timeout."""
    return _probe_put(f"/cameras/{camera_id(cam)}/arch-zone", payload)


def _same_camera_timestamp(actual, expected: str) -> bool:
    """Compare instants without rejecting equivalent ISO timezone spellings."""
    if not isinstance(actual, str):
        return False
    try:
        actual_time = datetime.fromisoformat(actual)
        expected_time = datetime.fromisoformat(expected)
    except (TypeError, ValueError):
        return False
    return (
        actual_time.utcoffset() is not None
        and expected_time.utcoffset() is not None
        and actual_time == expected_time
    )


def _canonical_request_id(request_id: UUID | str) -> str:
    """UUID запроса распознавания строго в каноническом виде — ключ идемпотентности."""
    raw = str(request_id)
    try:
        parsed = UUID(raw)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("request_id must be a canonical UUID") from exc
    if str(parsed) != raw:
        raise ValueError("request_id must be a canonical UUID")
    return raw


def _recognize_vehicle_from_camera(
    cam: str,
    request_id: UUID | str,
    stable_weight_at: str,
    *,
    retry_only: bool,
) -> dict:
    camera = camera_id(cam)
    raw_request_id = _canonical_request_id(request_id)
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
        raise _error_from_payload(status, payload)

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
            field in payload and (
                not _same_camera_timestamp(payload[field], expected)
                if field == "stable_weight_at"
                else payload[field] != expected
            )
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
    if (
        isinstance(votes, bool)
        or not isinstance(votes, int)
        or votes < 1
        or votes > MAX_VEHICLE_CONFIRMATION_VOTES
        or unit_interval(confirmation.get("detector_confidence")) is None
        or unit_interval(confirmation.get("ocr_confidence")) is None
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
JPEG_MAGIC = b"\xff\xd8\xff"


ORIENTATION_SAMPLE_TIMEOUT = 20.0


def _orientation_done(status: int) -> bool:
    """Успех эндпоинтов датасета — только 2xx.

    urllib не следует редиректам для POST с телом и DELETE: 3xx приходит
    сюда кортежем со статусом, а кадр при этом не сохранён и не удалён.
    """

    return 200 <= status < 300


def post_orientation_sample(
    *,
    sample_id: str,
    label: str,
    jpeg: bytes,
    weight_kg: int | None = None,
    captured_at: str | None = None,
    source: str = "crm",
) -> dict:
    """Hand one labelled scale-camera frame to Camera-PC for self-training."""

    if label not in VEHICLE_ORIENTATIONS:
        raise ValueError("label must be front or rear")
    headers = {
        "X-Sample-Id": str(sample_id),
        "X-Sample-Label": label,
        "X-Sample-Source": source,
    }
    if weight_kg is not None:
        headers["X-Sample-Weight-Kg"] = str(int(weight_kg))
    if captured_at:
        headers["X-Sample-Captured-At"] = str(captured_at)
    status, payload = _request(
        "POST",
        "/vehicle-orientation/samples",
        raw_body=bytes(jpeg),
        content_type="image/jpeg",
        extra_headers=headers,
        timeout_seconds=ORIENTATION_SAMPLE_TIMEOUT,
    )
    if not _orientation_done(status):
        raise _error_from_payload(status, payload)
    return payload


def delete_orientation_sample(sample_id: str) -> bool:
    """Remove one training frame from Camera-PC; ``False`` when it had none.

    Только 2xx — удалено, 404 — ПК такого кадра не держит; всё остальное
    (в том числе 3xx) — ``AiError``, кадр считается оставшимся на ПК.
    """

    status, payload = _request(
        "DELETE",
        f"/vehicle-orientation/samples/{sample_id}",
        timeout_seconds=ORIENTATION_SAMPLE_TIMEOUT,
    )
    if status == 404:
        return False
    if not _orientation_done(status):
        raise _error_from_payload(status, payload)
    return bool(payload.get("removed", True))


def clear_orientation_samples() -> int:
    """Стереть весь датасет на Camera-PC одним запросом; вернуть число кадров.

    ``DELETE /vehicle-orientation/samples`` без идентификатора. Любой ответ
    не 2xx (в том числе 404 на старой прошивке ПК без этого эндпоинта и 3xx)
    — ``AiError``: вызывающий откатывается на удаление по одному кадру.
    """

    status, payload = _request(
        "DELETE",
        "/vehicle-orientation/samples",
        timeout_seconds=ORIENTATION_SAMPLE_TIMEOUT,
    )
    if not _orientation_done(status):
        raise _error_from_payload(status, payload)
    try:
        return int(payload.get("removed", 0))
    except (TypeError, ValueError):
        return 0


def vehicle_orientation_info() -> dict:
    """Dataset size, model and last training report of the orientation classifier."""

    return _probe_get("/vehicle-orientation")


def fetch_vehicle_recognition_frame(cam: str, request_id: UUID | str) -> bytes | None:
    """Download the evidence JPEG Camera-PC kept for one recognition request.

    ``None`` means "no photo" (unknown request, retention expired, or the
    camera PC is unreachable). Callers treat the photo as best-effort audit
    material and never let its absence change accounting.
    """

    camera = camera_id(cam)
    raw_request_id = _canonical_request_id(request_id)
    try:
        return _fetch_jpeg(
            f"/cameras/{camera}/vehicle-recognition/{raw_request_id}/frame",
            timeout=VEHICLE_FRAME_TIMEOUT,
            max_bytes=VEHICLE_FRAME_MAX_BYTES,
        )
    except AiError as exc:
        if exc.status == 404:
            return None
        raise


def _fetch_jpeg(path: str, *, timeout: float, max_bytes: int) -> bytes:
    """GET one bounded JPEG from Camera-PC; an HTTP error becomes ``AiError``."""

    request = urllib.request.Request(
        f"{AI_URL}{path}",
        method="GET",
        headers={"X-Api-Key": AI_KEY, "Accept": "image/jpeg"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise AiError(exc.code, f"AI-сервис: ошибка {exc.code}") from exc
    except (http.client.HTTPException, TimeoutError, OSError) as exc:
        raise AiUnavailable(str(exc)) from exc
    try:
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0]
        if content_type.strip().lower() != "image/jpeg":
            raise AiProtocolError("AI-сервис вернул кадр в неожиданном формате")
        try:
            data = response.read(max_bytes + 1)
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            raise AiUnavailable(str(exc)) from exc
    finally:
        response.close()
    if len(data) > max_bytes:
        raise AiProtocolError("AI-сервис вернул слишком большой кадр")
    if not data.startswith(JPEG_MAGIC):
        raise AiProtocolError("AI-сервис вернул некорректный кадр")
    return bytes(data)


CAMERA_LINE_FRAME_TIMEOUT = 8.0
CAMERA_LINE_FRAME_MAX_BYTES = 8 * 1024 * 1024
CAMERA_LINE_FRAME_ERRORS = {
    404: (
        "AI-сервис не отдаёт кадр этой камеры: камеры нет в его списке "
        "или AI-сервис нужно обновить"
    ),
    503: (
        "Нет свежего кадра: камера не подключена к AI. "
        "Запустите камеру и обновите кадр"
    ),
}


def counting_line_frame(cam: str) -> bytes:
    """Latest still frame of an AI-connected camera for drawing its lines."""

    path = f"/cameras/{camera_id(cam)}/frame"
    try:
        return _fetch_jpeg(
            path,
            timeout=CAMERA_LINE_FRAME_TIMEOUT,
            max_bytes=CAMERA_LINE_FRAME_MAX_BYTES,
        )
    except AiError as exc:
        detail = CAMERA_LINE_FRAME_ERRORS.get(exc.status)
        # 401/5xx are CRM↔Camera-PC faults: never hand the browser a 401 that
        # its session interceptor would mistake for an expired login.
        raise AiError(
            exc.status if detail else 502,
            detail or exc.detail,
        ) from exc


def status(cam: str) -> dict | None:
    """Статус и живой счётчик; None — модель на камере не запущена."""
    return _call_optional("GET", _path(cam))


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


def is_running_order_session(payload: Mapping) -> bool:
    """The worker is counting an order session right now."""
    return payload.get("running") is True and payload.get("mode") == "session"


def is_continuous_shipping(payload: Mapping) -> bool:
    """The payload comes from the uninterrupted shipping processor.

    Order counting may only attach to it: a cold or AI-24/7 session would put
    the same physical crossing into the wrong business ledger.
    """
    return (
        payload.get("continuous_analytics") is True
        # Literal, not models.ANALYTICS_SCOPE_SHIPPING: the weighbridge
        # collector imports this client without Django apps or models.
        and payload.get("analytics_scope") == "shipping"
    )


def _order_session_ready(
    payload: object,
    *,
    expected_session_id: int | None,
) -> bool:
    if not isinstance(payload, Mapping) or not is_running_order_session(payload):
        return False
    assert_order_session_identity(payload, expected_session_id)
    if not is_continuous_shipping(payload):
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
    return _call("POST", _path(cam), body=options or {})


def delete(cam: str, session_id: int | None = None) -> dict | None:
    """Перевести уже сохранённую сессию в IDLE, не делая предварительный GET."""
    body = {"session_id": session_id} if session_id is not None else None
    return _call_optional("DELETE", _path(cam), body=body)


def always_on_status() -> dict:
    """Desired 24/7 cameras and their live inference-only processors."""
    return _call("GET", "/always-on")


def count_events(
    cam: str,
    after_id: int,
    limit: int = 500,
) -> dict:
    """Read one ordered page from the camera-PC durable count journal.

    Every HTTP error, including 404, is an ``AiError``: an uncertain event
    stream is a sync failure, never a reason to skip or guess counts.
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
    return _call(
        "GET",
        f"/events?{query}",
        max_response_bytes=EVENT_PAGE_MAX_BYTES,
    )


def _cached(key: str, ttl: int, fetch: Callable[[], dict]) -> dict:
    """Снимок ПК цеха из кэша на ``ttl`` секунд.

    Отказ (AiUnavailable/AiError) кэшируется так же: при выключенном ПК цеха
    частый опрос иначе копил бы полные ``TIMEOUT``.
    """
    cached = cache.get(key)
    if isinstance(cached, Exception):
        raise cached
    if cached is not None:
        return cached
    try:
        result = fetch()
    except (AiUnavailable, AiError) as outage:
        cache.set(key, outage, ttl)
        raise
    cache.set(key, result, ttl)
    return result


def always_on_status_cached() -> dict:
    return _cached(ALWAYS_ON_CACHE_KEY, ALWAYS_ON_TTL, always_on_status)


def _detections_payload(status: dict) -> dict:
    return {
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


def always_on_detections_cached() -> dict:
    """Только рамки процессоров — лёгкий ответ для частого опроса монитора.

    Отдаётся тем же вызовом ``/always-on``, но со своим коротким TTL: экран
    тянет рамки раз в секунду, а тяжёлые настройки и аналитика продолжают
    жить на общем пятисекундном снимке.

    Отрицательный результат кэшируется так же, как в общем снимке.
    """
    return _cached(
        DETECTIONS_CACHE_KEY,
        DETECTIONS_TTL,
        lambda: _detections_payload(always_on_status()),
    )


def cached_always_on_status() -> dict | None:
    """Уже готовый снимок, либо None — никогда не ходит по сети.

    Для необязательных подсказок (например, предварительной проверки лимита
    процессоров) ожидание ``TIMEOUT`` неоправданно: без снимка просто
    пропускаем подсказку, а авторитетное решение принимает сам ПК цеха.
    """
    cached = cache.get(ALWAYS_ON_CACHE_KEY)
    return None if isinstance(cached, Exception) else cached


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
    status = _call(
        "PUT",
        "/always-on",
        {
            "camera_sources": normalized,
            "source": source,
            "analytics_scopes": normalized_scopes,
        },
    )
    cache.set(ALWAYS_ON_CACHE_KEY, status, ALWAYS_ON_TTL)
    cache.delete(DETECTIONS_CACHE_KEY)
    return status


def camera_frame_jpeg(
    stream: str,
    *,
    timeout: float = WAGON_PLATE_TIMEOUT,
    max_bytes: int = WAGON_PLATE_MAX_BYTES,
) -> bytes | None:
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            frame = response.read(max_bytes + 1)
    except (OSError, TimeoutError, urllib.error.URLError):
        return None
    if len(frame) > max_bytes or not frame.startswith(JPEG_MAGIC):
        # Не JPEG или больше лимита сервиса — отправлять такое бессмысленно.
        return None
    return frame


# Модели номеров на ПК камер: путь эндпоинта и задача, которую сервис пишет
# в ответ при включённом OCR.
NUMBER_MODELS = {
    "vehicle_number": ("/vehicle-number/detect", "vehicle_plate_recognition"),
    "wagon_number": ("/wagon-number/detect", "wagon_number_recognition"),
}


def valid_transport_number(value: object, recognition_model: str) -> str:
    """Номер машины или вагона в каноническом виде, иначе "".

    Снимаются только пробелы и дефисы (и «KZ» у машины); у вагона сверяется
    контрольная цифра.
    """
    if not isinstance(value, str):
        return ""
    number = re.sub(r"[\s-]+", "", value.upper())
    if recognition_model == "vehicle_number":
        return kz_vehicle_plate(number)
    return number if recognition_model == "wagon_number" and wagon_check_digit_ok(number) else ""


def detect_number(model: str, frame: bytes) -> dict:
    """Отправить кадр JPEG модели номеров. Ответ разбирает number_from_payload."""
    path, _ = NUMBER_MODELS[model]
    status, payload = _request(
        "POST",
        path,
        raw_body=frame,
        content_type="image/jpeg",
        timeout_seconds=WAGON_PLATE_TIMEOUT,
    )
    if status != 200:
        raise AiError(status, "Модель распознавания номера недоступна")
    return payload


def _invalid_number_response() -> AiProtocolError:
    return AiProtocolError(
        "Модель вернула некорректный результат распознавания номера"
    )


def _accepted_number(detection: object, recognition_model: str) -> str | None:
    if not isinstance(detection, Mapping):
        raise _invalid_number_response()
    ocr = detection.get("ocr")
    if not isinstance(ocr, Mapping) or not isinstance(ocr.get("accepted"), bool):
        raise _invalid_number_response()
    if not ocr["accepted"]:
        return None

    # Both upstream services put the canonical number on this detection.
    # Wagon OCR supplies `digits`, not `number`; the top-level `number` is only
    # the first detection and must never stand in for another plate's result.
    number = detection.get("number")
    if not isinstance(number, str) or not number:
        raise _invalid_number_response()
    ocr_number = ocr.get("digits" if recognition_model == "wagon_number" else "number")
    if not isinstance(ocr_number, str) or ocr_number != number:
        raise _invalid_number_response()
    if (
        unit_interval(detection.get("confidence")) is None
        or unit_interval(ocr.get("confidence")) is None
    ):
        raise _invalid_number_response()

    if recognition_model == "wagon_number":
        length_valid = ocr.get("length_valid")
        checksum_valid = ocr.get("checksum_valid")
        if (
            not isinstance(length_valid, bool)
            or "checksum_valid" not in ocr
            or (checksum_valid is not None and not isinstance(checksum_valid, bool))
        ):
            raise _invalid_number_response()
        # Diagnostic wagon OCR marks any sufficiently confident digit string
        # accepted. Match its automatic consensus by requiring the declared
        # length and checksum as well before offering a number to the operator.
        if not length_valid or checksum_valid is not True:
            return None
        if not is_wagon_number(number):
            raise _invalid_number_response()
    elif VEHICLE_PLATE_RE.fullmatch(number) is None:
        raise _invalid_number_response()
    return number


def number_from_payload(payload: object, recognition_model: str) -> str | None:
    """Единственный номер, который модель приняла сама, иначе ``None``.

    Строгий разбор ответа /…-number/detect для всех потребителей: номер вагона
    засчитывается только с верной длиной и контрольной суммой. Выключенный OCR
    — AiError 503, ответ не по контракту — AiProtocolError.
    """
    _, expected_task = NUMBER_MODELS[recognition_model]
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise _invalid_number_response()
    if payload.get("ocr") is False:
        raise AiError(
            503, "Распознавание текста номера не включено в выбранной модели"
        )
    if payload.get("ocr") is not True or payload.get("task") != expected_task:
        raise _invalid_number_response()
    detections = payload.get("detections")
    if not isinstance(detections, list):
        raise _invalid_number_response()
    numbers = {
        number
        for detection in detections
        if (number := _accepted_number(detection, recognition_model)) is not None
    }
    return next(iter(numbers)) if len(numbers) == 1 else None


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
        payload = detect_number("wagon_number", frame)
    except (AiUnavailable, AiError):
        return None
    detections = payload.get("detections")
    if not isinstance(detections, list):
        return None
    try:
        number = number_from_payload(payload, "wagon_number") or ""
    except (AiProtocolError, AiError):
        # OCR выключен или ответ не по контракту: табличка видна, приход
        # заводится без номера. Непроверенный номер в учёт не пишем — чужой
        # вагон хуже незаполненного поля.
        number = ""
    return {"seen": bool(detections), "number": number}
