"""Динамическое обнаружение камер в MediaMTX на ПК с камерами.

Пути cam1..camN генерируются на ПК автоматически по числу каналов NVR
(число меняется), поэтому список нельзя хардкодить. Обнаружение — RTSP
DESCRIBE с Basic-авторизацией на каждый путь:
  200 → камера в строю; 404 → путь есть, источник лежит; 400 → пути нет.
Результат кэшируется, чтобы не дёргать MediaMTX на каждый запрос.
"""
import base64
import logging
import os
import socket
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache

log = logging.getLogger(__name__)

# `or`, а не второй аргумент get: пустая строка из compose не должна
# перетирать дефолт.
CAMERA_HOST = os.environ.get("CAMERA_HOST") or "100.109.156.107"
CAMERA_PORT = int(os.environ.get("CAMERA_PORT") or "8554")
CAMERA_USER = os.environ.get("CAMERA_USER") or "viewer"
CAMERA_PASS = os.environ.get("CAMERA_PASS", "")

# Верхняя граница перебора camN; go2rtc пре-провижен на столько же потоков.
MAX_CAMERAS = 32
PROBE_TIMEOUT = 12  # сек; on-demand источник у MediaMTX поднимается 2–10 с
CACHE_KEY = "cameras:discovered"
CACHE_TTL = 240  # сек; на ПК список путей обновляется раз в ~5 мин

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


def discover_cameras() -> list[dict]:
    """Актуальный список камер (кэшируется на CACHE_TTL секунд)."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    if not CAMERA_PASS:
        log.warning("CAMERA_PASS не задан — обнаружение камер пропущено")
        return []

    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(_probe_path, (f"cam{n}sub" for n in range(1, MAX_CAMERAS + 1))))

    cameras = [
        {
            "id": n,
            "name": f"Камера {n}",
            "zone": ZONES.get(n, f"Камера {n}"),
            "src": f"cam{n}",
        }
        for n, status in enumerate(statuses, start=1)
        if status in ("online", "offline")
    ]
    cache.set(CACHE_KEY, cameras, CACHE_TTL)
    return cameras
