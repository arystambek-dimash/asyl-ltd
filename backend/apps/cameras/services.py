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
import re
import socket
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.cache import cache

from . import ai

log = logging.getLogger(__name__)

# Environment variables are parsed once in Django settings. These module-level
# names remain the camera discovery dependency boundary and are easy to replace
# in focused tests.
CAMERA_HOST = settings.CAMERA_HOST
CAMERA_PORT = settings.CAMERA_PORT
CAMERA_USER = settings.CAMERA_USER
CAMERA_PASS = settings.CAMERA_PASS

# Столько camN-слотов захардкожено в go2rtc.yaml (вместе с camNmain);
# он же — верхняя граница резервного перебора.
MAX_CAMERAS = 32
PROBE_TIMEOUT = 12  # сек; on-demand источник у MediaMTX поднимается 2–10 с
# Снимок последнего обновления (реальный список или офлайн-вид при сбое)
# живёт долго, а его свежесть — отдельным ключом: протухший снимок отдаётся
# как есть, пока фон обновляет его (stale-while-revalidate). Простое
# истечение срока — не сбой связи и не должно гасить всю стену.
CACHE_KEY = "cameras:discovered:v6"
FRESH_KEY = "cameras:discovered-fresh:v6"
CACHE_TTL = 240  # сек свежести; инвентарь на ПК обновляется раз в ~5 мин
LAST_GOOD_CACHE_KEY = "cameras:last-good:v6"
LAST_GOOD_TTL = 7 * 24 * 3600
EMPTY_CACHE_TTL = 15  # сбой перепроверяется часто, чтобы стена быстро ожила
# Один воркер за раз выполняет дорогое обнаружение. Остальные сразу получают
# последний снимок, поэтому недоступный ПК цеха не превращается в очередь
# gunicorn-воркеров, ждущих сетевых таймаутов.
REFRESH_LOCK_KEY = "cameras:discovering:v6"
# Пробы (до 2 волн по 12 с) плюс запрос инвентаря; замок снимается сам, если
# воркер умер, и не может «залипнуть» дольше одного полного обнаружения.
REFRESH_LOCK_TTL = 60

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

CAMERA_PATH_RE = re.compile(r"^cam(?:[1-9][0-9]*|_[A-Za-z0-9]{4,32})$")
COUNTING_LINE_CONFIG_FIELDS = (
    "cam",
    "configured",
    "coordinate_space",
    "line",
    "line_spec",
    "direction",
    "verification_lines",
    "updated_at",
)


def normalize_camera_path(value: str) -> str:
    """Safe wall path; unlike AI processors this also permits direct cameras."""
    path = str(value).strip()
    if path.isdigit():
        path = f"cam{path}"
    if not CAMERA_PATH_RE.fullmatch(path):
        raise ValueError("unknown camera path")
    return path


def update_cached_counting_line(camera: str, payload: dict) -> None:
    """Patch inventory snapshots after an authoritative line save.

    Camera discovery is intentionally cached for minutes because it can probe
    the shop-floor network. A line edit must not inherit that delay, so update
    only the matching camera in both snapshots without triggering discovery.
    """
    camera = normalize_camera_path(camera)
    config = {
        field: payload.get(field)
        for field in COUNTING_LINE_CONFIG_FIELDS
        if field in payload
    }
    if config.get("configured") is not True or not isinstance(
        config.get("line"), dict
    ):
        return

    for key in (CACHE_KEY, LAST_GOOD_CACHE_KEY):
        snapshot = cache.get(key)
        if not isinstance(snapshot, list):
            continue
        changed = False
        updated = []
        for item in snapshot:
            if isinstance(item, dict) and item.get("src") == camera:
                updated.append({**item, "line_config": dict(config)})
                changed = True
            else:
                updated.append(item)
        if changed:
            cache.set(key, updated, LAST_GOOD_TTL)


def _offline_view(cameras: list[dict]) -> list[dict]:
    return [
        {
            **camera,
            "online": False,
            "note": camera.get("note") or "Связь потеряна, выполняется переподключение",
        }
        for camera in cameras
    ]


def _merge_probe_status(last_good: list[dict], probed: list[dict]) -> list[dict]:
    """Живой статус NVR-каналов из проб поверх топологии инвентаря.

    Пробы знают только cam1..camN с id по номеру канала, без direct/locked
    камер и линий подсчёта, поэтому топологию не заменяют: берётся только
    online по src, остальные камеры инвентаря показываются как потерявшие связь.
    """
    online = {camera["src"]: camera["online"] for camera in probed}
    return [
        {**camera, "online": online[camera.get("src")]}
        if camera.get("src") in online
        else _offline_view([camera])[0]
        for camera in last_good
    ]


def _store_snapshot(cameras: list[dict], fresh_ttl: int) -> list[dict]:
    cache.set(CACHE_KEY, cameras, LAST_GOOD_TTL)
    cache.set(FRESH_KEY, True, fresh_ttl)
    return cameras


def _refresh_cameras() -> list[dict]:
    """Дорогое обнаружение: запрос инвентаря и/или RTSP-пробы."""
    last_good = cache.get(LAST_GOOD_CACHE_KEY) or []
    cameras = _discover_by_inventory()
    # Last-good пишется только из инвентаря или из проб, когда инвентаря нет
    # вовсе. Пока настроенный инвентарь временно недоступен, урезанный
    # результат проб не должен затирать топологию по MAC.
    authoritative = cameras is not None or not ai.enabled()
    if cameras is None:
        cameras = _discover_by_probe()
        if cameras and not authoritative and last_good:
            cameras = _merge_probe_status(last_good, cameras)

    if cameras:
        if authoritative:
            cache.set(LAST_GOOD_CACHE_KEY, cameras, LAST_GOOD_TTL)
        return _store_snapshot(cameras, CACHE_TTL)

    return _store_snapshot(_offline_view(last_good), EMPTY_CACHE_TTL)


def _refresh_in_background() -> None:
    """Одно фоновое обновление за раз: параллельные запросы не множат пробы."""
    if not cache.add(REFRESH_LOCK_KEY, "1", REFRESH_LOCK_TTL):
        return

    def refresh() -> None:
        try:
            _refresh_cameras()
        except Exception:  # фоновая изоляция
            log.exception("Фоновое обновление списка камер не удалось")
        finally:
            cache.delete(REFRESH_LOCK_KEY)

    threading.Thread(target=refresh, name="camera-discovery", daemon=True).start()


def discover_cameras() -> list[dict]:
    """Актуальный список камер с last-known-good fallback.

    Обнаружение ходит по сети к ПК цеха: недоступный инвентарь стоит таймаута,
    а резервные RTSP-пробы — ещё до двух волн по ``PROBE_TIMEOUT``. Держать на
    этом HTTP-запрос нельзя: страница моноблока опрашивает камеры по кругу и
    зависала бы на «Загрузка…» каждый раз, когда цех офлайн.

    Поэтому ожидание сети допускается только когда показать вообще нечего.
    Иначе отдаётся последний снимок как есть — реальный список или офлайн-вид
    после сбоя, — а протухший обновляется в фоне. Если снимок потерян
    (сброс кэша), известная топология показывается с ``online=False``:
    плееры сами переподключаются и оживают после обновления.
    """
    snapshot = cache.get(CACHE_KEY)
    if snapshot is not None and cache.get(FRESH_KEY):
        return snapshot

    if snapshot is None:
        last_good = cache.get(LAST_GOOD_CACHE_KEY)
        if not last_good:
            # Первый запуск: показать нечего, приходится дождаться обнаружения.
            return _refresh_cameras()
        snapshot = _offline_view(last_good)

    _refresh_in_background()
    return snapshot


# --- основной путь: инвентарь ai_service -----------------------------------

def _discover_by_inventory() -> list[dict] | None:
    """Камеры из инвентаря ai_service; None — сервис недоступен/не настроен."""
    if not ai.enabled():
        return None
    try:
        inventory = ai.inventory()
        devices = inventory.get("devices") or []
        line_configs = inventory.get("line_configs") or {}
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
                "line_config": line_configs.get(path),
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
                "line_config": line_configs.get(path),
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
    cameras.sort(key=lambda c: (order.get(c["kind"], 3), _natural(c["src"] or c["name"])))
    _sync_go2rtc(sync)
    return cameras


def _natural(s: str) -> tuple:
    """Натуральный порядок: cam10 после cam2, а не между cam1 и cam2."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s))


def _static_slot(path: str) -> bool:
    """cam1..cam32 (и их camNmain) уже прописаны в go2rtc.yaml."""
    m = re.fullmatch(r"cam(\d+)", path)
    if m is None:
        return False
    return 1 <= int(m.group(1)) <= MAX_CAMERAS


def _sync_go2rtc(pairs: list[tuple[str, str]]) -> None:
    """Дозаявить в go2rtc потоки вне статик-конфига (direct-камеры, camN>32).

    PUT /api/streams идемпотентен; go2rtc держит их до рестарта, а рестарт
    роняет и кэш списка — следующий discover заявит заново. Ошибки не валят
    список: без записи в go2rtc плитка просто останется «Нет сигнала».

    Сабпоток отдаётся браузеру как есть (обычно H.264, транскод не нужен);
    ffmpeg-источник go2rtc поднимет сам, только если кодек консюмеру не
    подошёл (та же схема, что у статик-слотов в go2rtc.yaml).
    """
    if not ai.GO2RTC_API:
        return
    base = f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_HOST}:{CAMERA_PORT}"
    for path, sub in pairs:
        if _static_slot(path):
            continue
        _go2rtc_put(path, f"{base}/{sub}", f"ffmpeg:{path}#video=h264")
        if ai.CAM_RE.fullmatch(path):
            # MediaMTX inventory's path is the main stream; sub is a separate
            # path. OCR and its preview must use the same full-resolution frame.
            _go2rtc_put(
                f"{path}main", f"{base}/{path}", f"ffmpeg:{path}main#video=h264"
            )


def _go2rtc_put(name: str, *srcs: str) -> None:
    q = urllib.parse.urlencode([("name", name), *(("src", s) for s in srcs)])
    req = urllib.request.Request(f"{ai.GO2RTC_API}/api/streams?{q}", method="PUT")
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
