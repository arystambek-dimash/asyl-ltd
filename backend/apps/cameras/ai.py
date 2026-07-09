"""Клиент AI-подсчёта мешков (ai_service.py на ПК с камерами).

Модель включается per-камера по HTTP: POST /processors/cam<N> поднимает
обработчик и публикует в MediaMTX аннотированный поток cam<N>ai, DELETE
выключает его. Счётчик живёт в памяти обработчика — финальное число нужно
забирать ДО выключения.

Планшеты поста не в Tailscale и до ai_service не достают, поэтому все
вызовы идут через бэкенд, а ключ API не покидает сервер.
"""
import json
import os
import re
import urllib.error
import urllib.request

from .services import CAMERA_HOST

AI_URL = (os.environ.get("CAMERA_AI_URL") or f"http://{CAMERA_HOST}:8890").rstrip("/")
AI_KEY = os.environ.get("CAMERA_AI_KEY", "")
TIMEOUT = 10  # сек; запуск модели асинхронный, долгих ответов у API нет

# Имена камер у ai_service/MediaMTX: cam2 (канал NVR) или cam_8c26 (direct
# по хвосту MAC). Валидация обязательна — имя попадает в URL запроса.
CAM_RE = re.compile(r"^cam\w{1,16}$")


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
    """Имя камеры к виду ai_service: «2» → cam2; cam_8c26 — как есть."""
    cam = str(cam).strip()
    if cam.isdigit():
        cam = f"cam{cam}"
    if not CAM_RE.fullmatch(cam):
        raise AiError(400, "Неизвестная камера")
    return cam


def _path(cam: str) -> str:
    return f"/processors/{normalize(cam)}"


def inventory() -> dict:
    """Живой инвентарь сети цеха: devices (nvr-channel/direct/locked) + ai."""
    return _call("GET", "/cameras") or {}


def status(cam: str) -> dict | None:
    """Статус и живой счётчик; None — модель на камере не запущена."""
    return _call("GET", _path(cam), none_on_404=True)


def start(cam: str, options: dict | None = None) -> dict:
    """Включить модель. options — source/line/direction, дефолты ai_service."""
    return _call("POST", _path(cam), body=options or {})


def reset(cam: str) -> dict:
    """Обнулить счётчик работающей модели (новая погрузка)."""
    return _call("POST", f"{_path(cam)}/reset")


def stop(cam: str) -> dict | None:
    """Выключить модель; возвращает финальный счётчик (None — не была запущена)."""
    final = status(cam)
    if final is None:
        return None
    _call("DELETE", _path(cam), none_on_404=True)  # гонка выключений — не ошибка
    return final
