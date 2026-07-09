"""Обнаружение камер цеха.

Основной источник — живой инвентарь ai_service (`GET /cameras` на ПК с
камерами): каждые 5 минут он сканирует сеть и знает все устройства —
каналы NVR, direct-камеры (стабильный путь по MAC) и «locked» (физически
в сети, но пароль неизвестен). Список на сайте строится из него: ровно
то, что реально подключено, с привязкой по MAC, а не по номерам каналов.

Резерв на случай недоступности ai_service — старый перебор cam1..camN
RTSP DESCRIBE-пробами прямо в MediaMTX:
  200 → камера в строю; 404 → путь есть, источник лежит; 400 → пути нет.

Результат кэшируется, чтобы не дёргать ПК на каждый запрос.
"""
import base64
import logging
import os
import re
import socket
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache

log = logging.getLogger(__name__)

# `or`, а не второй аргумент get: пустая строка из compose не должна
# перетирать дефолт.
CAMERA_HOST = os.environ.get("CAMERA_HOST") or "100.109.156.107"
CAMERA_PORT = int(os.environ.get("CAMERA_PORT") or "8554")
CAMERA_USER = os.environ.get("CAMERA_USER") or "viewer"
CAMERA_PASS = os.environ.get("CAMERA_PASS", "")
# Внутренний API go2rtc (в docker-сети) — для дозаявки динамических потоков.
GO2RTC_API = (os.environ.get("GO2RTC_API_URL") or "").rstrip("/")

# Столько camN-слотов захардкожено в go2rtc.yaml (вместе с camNai);
# он же — верхняя граница резервного перебора.
MAX_CAMERAS = 32
PROBE_TIMEOUT = 12  # сек; on-demand источник у MediaMTX поднимается 2–10 с
CACHE_KEY = "cameras:discovered:v2"  # v2 — строковые id и поля инвентаря
CACHE_TTL = 240  # сек; инвентарь на ПК обновляется раз в ~5 мин

# Известные зоны цеха по номерам каналов NVR; для остальных — «Камера N».
ZONES = {
    1: "Въезд / весы",
    2: "Зона загрузки",
    3: "Ворота",
    4: "Склад",
    5: "Производство",
    6: "Двор",
    7: "Мельница",
    8: "Периметр",
}


def discover_cameras() -> list[dict]:
    """Актуальный список камер (кэшируется на CACHE_TTL секунд)."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    cameras = _discover_by_inventory()
    if cameras is None:
        cameras = _discover_by_probe()
    cache.set(CACHE_KEY, cameras, CACHE_TTL)
    return cameras


# --- основной путь: инвентарь ai_service -----------------------------------

def _discover_by_inventory() -> list[dict] | None:
    """Камеры из инвентаря ai_service; None — сервис недоступен/не настроен."""
    from . import ai  # локальный импорт: ai.py использует наши константы

    if not ai.enabled():
        return None
    try:
        devices = ai.inventory().get("devices") or []
    except (ai.AiUnavailable, ai.AiError) as e:
        log.warning("Инвентарь камер недоступен (%s) — резервные RTSP-пробы", e)
        return None

    cameras: list[dict] = []
    sync: list[tuple[str, str]] = []  # (path, sub) для дозаявки в go2rtc
    for d in devices:
        kind = d.get("kind")
        path = d.get("path") or ""
        if kind == "nvr-channel" and path:
            ch = d.get("channel")
            cameras.append({
                # id по MAC — стабилен при перетасовке каналов NVR и смене IP
                "id": f"nvr:{d.get('mac') or path}",
                "name": d.get("model") or f"Камера {ch}",
                "zone": ZONES.get(ch, f"Камера {ch}"),
                "src": path,
                "kind": kind,
                "online": bool(d.get("online", True)),
            })
            sync.append((path, d.get("sub") or path))
        elif kind == "direct" and path:
            cameras.append({
                "id": f"direct:{d.get('mac') or path}",
                "name": d.get("model") or path,
                "zone": path.replace("cam_", "Камера "),
                "src": path,
                "kind": kind,
                "online": bool(d.get("online", True)),
            })
            sync.append((path, d.get("sub") or path))
        elif kind == "locked":
            ip = d.get("ip") or "?"
            cameras.append({
                "id": f"locked:{ip}",
                "name": ip,
                "zone": "Нет доступа",
                "src": None,
                "kind": kind,
                "online": False,
                "note": d.get("note") or "Камера обнаружена, нет доступа",
            })

    order = {"nvr-channel": 0, "direct": 1, "locked": 2}
    cameras.sort(key=lambda c: (order.get(c["kind"], 3), c["src"] or c["name"]))
    _sync_go2rtc(sync)
    return cameras


def _static_slot(path: str) -> bool:
    """cam1..cam32 (и их camNai) уже прописаны в go2rtc.yaml."""
    m = re.fullmatch(r"cam(\d+)", path)
    return bool(m) and 1 <= int(m.group(1)) <= MAX_CAMERAS


def _sync_go2rtc(pairs: list[tuple[str, str]]) -> None:
    """Дозаявить в go2rtc потоки вне статик-конфига (direct-камеры, camN>32).

    PUT /api/streams идемпотентен; go2rtc держит их до рестарта, а рестарт
    роняет и кэш списка — следующий discover заявит заново. Ошибки не валят
    список: без записи в go2rtc плитка просто останется «Нет сигнала».
    """
    if not GO2RTC_API:
        return
    base = f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_HOST}:{CAMERA_PORT}"
    for path, sub in pairs:
        if _static_slot(path):
            continue
        for name, src in (
            (f"{path}src", f"{base}/{sub}"),
            (path, f"ffmpeg:{path}src#video=h264"),  # HEVC → H.264 для браузера
            (f"{path}ai", f"{base}/{path}ai"),
        ):
            _go2rtc_put(name, src)


def _go2rtc_put(name: str, src: str) -> None:
    q = urllib.parse.urlencode({"name": name, "src": src})
    req = urllib.request.Request(f"{GO2RTC_API}/api/streams?{q}", method="PUT")
    try:
        urllib.request.urlopen(req, timeout=3).close()
    except OSError as e:
        log.warning("go2rtc: не удалось добавить поток %s: %s", name, e)


# --- резерв: RTSP-пробы MediaMTX --------------------------------------------

def _probe_path(path: str) -> str:
    """RTSP DESCRIBE к MediaMTX. Возвращает online | offline | absent."""
    url = f"rtsp://{CAMERA_HOST}:{CAMERA_PORT}/{path}"
    auth = base64.b64encode(f"{CAMERA_USER}:{CAMERA_PASS}".encode()).decode()
    req = (
        f"DESCRIBE {url} RTSP/1.0\r\n"
        "CSeq: 1\r\n"
        f"Authorization: Basic {auth}\r\n"
        "Accept: application/sdp\r\n\r\n"
    )
    try:
        with socket.create_connection((CAMERA_HOST, CAMERA_PORT), timeout=PROBE_TIMEOUT) as s:
            s.settimeout(PROBE_TIMEOUT)
            s.sendall(req.encode())
            status_line = s.recv(1024).decode(errors="replace").split("\r\n", 1)[0]
    except OSError:
        return "absent"
    if " 200 " in status_line:
        return "online"
    if " 404 " in status_line:
        return "offline"  # путь настроен, но NVR/канал сейчас не отдаёт поток
    return "absent"


def _discover_by_probe() -> list[dict]:
    """Резервный перебор cam1..camN, когда ai_service недоступен."""
    if not CAMERA_PASS:
        log.warning("CAMERA_PASS не задан — обнаружение камер пропущено")
        return []

    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(_probe_path, (f"cam{n}sub" for n in range(1, MAX_CAMERAS + 1))))

    return [
        {
            "id": f"nvr:cam{n}",
            "name": f"Камера {n}",
            "zone": ZONES.get(n, f"Камера {n}"),
            "src": f"cam{n}",
            "kind": "nvr-channel",
            "online": status == "online",
        }
        for n, status in enumerate(statuses, start=1)
        if status in ("online", "offline")
    ]
