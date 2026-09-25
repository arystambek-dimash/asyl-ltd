from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections, connections
from django.utils import timezone

from apps.common.locks import advisory_lock
from apps.grain import services as grain_services

from . import ai, event_sync
from .models import (
    AlwaysOnCounterCursor,
    MonoblockCameraSettings,
)

log = logging.getLogger(__name__)

_ALWAYS_ON_LOCAL_MUTEX = threading.RLock()
_ALWAYS_ON_ADVISORY_NAMESPACE = 0x4149  # "AI"
_ALWAYS_ON_ADVISORY_KEY = 0x323437  # "247"
_AI_NOT_CONFIGURED = "AI-сервис не настроен"


def _live_sources(current: dict) -> list[str] | None:
    live_sources = current.get("camera_sources")
    if not isinstance(live_sources, list) or any(
        not isinstance(source, str) for source in live_sources
    ):
        return None
    return live_sources


def _normalized_live_sources(current: dict) -> set[str] | None:
    """Cameras the camera PC reports as applied; ``None`` — state unknown."""

    live_sources = _live_sources(current)
    if live_sources is None:
        return None
    normalized: set[str] = set()
    for source in live_sources:
        try:
            normalized.add(ai.normalize(source))
        except ai.AiError:
            continue
    return normalized


def _processor_readiness(
    current: dict,
    camera: str,
    *,
    analytics_scope: str,
) -> tuple[str, str]:
    live_sources = _live_sources(current)
    if live_sources is None or camera not in live_sources:
        return "pending", f"Камера {camera} ещё не применена на AI-сервисе"
    if current.get("source") != "sub":
        return "pending", f"Камера {camera} ещё не перешла на источник sub"

    scopes = current.get("analytics_scopes")
    if not isinstance(scopes, dict) or scopes.get(camera) != analytics_scope:
        return "pending", f"AI-сервис не подтвердил роль камеры {camera}"

    for item in current.get("pending") or []:
        if not isinstance(item, dict) or item.get("cam") != camera:
            continue
        reason = item.get("reason")
        detail = reason if isinstance(reason, str) else "процессор запускается"
        return "pending", f"{camera}: {detail}"

    processors = current.get("processors")
    if not isinstance(processors, list):
        return "pending", "AI-сервис не подтвердил готовность процессоров"
    by_camera = {
        item.get("cam"): item
        for item in processors or []
        if isinstance(item, dict) and isinstance(item.get("cam"), str)
    }
    processor = by_camera.get(camera)
    if (
        processor is None
        or processor.get("running") is not True
        or processor.get("processor_alive") is not True
    ):
        return "pending", f"Ожидается запуск процессора {camera}"
    if processor.get("source") != "sub":
        return "pending", f"Процессор {camera} ещё не перешёл на источник sub"
    if processor.get("analytics_scope") != analytics_scope:
        return "pending", f"Процессор {camera} не подтвердил свою роль"
    mode = processor.get("mode")
    if mode not in {"always_on", "session"}:
        return "pending", f"AI-сервис не подтвердил режим процессора {camera}"
    if mode == "session" and processor.get("continuous_analytics") is not True:
        return "pending", f"Сессия {camera} потеряла непрерывную аналитику"
    explicit_status = processor.get("status")
    if explicit_status in {
        "reconnecting",
        "warming",
        "waiting",
        "starting",
        "failed",
        "model_unavailable",
        "stopped",
    }:
        return "pending", f"Камера {camera} сейчас в состоянии {explicit_status}"
    metrics = processor.get("metrics")
    gap_started_at = processor.get("camera_gap_started_at")
    if gap_started_at is None and isinstance(metrics, dict):
        gap_started_at = metrics.get("camera_gap_started_at")
    if gap_started_at:
        return "pending", f"Камера {camera} переподключается после потери потока"
    if not processor.get("last_frame_at"):
        return "pending", f"Камера {camera} ещё не передала кадр"
    if isinstance(metrics, dict) and "inference_frames" in metrics:
        inference_frames = metrics.get("inference_frames")
        if type(inference_frames) is not int or inference_frames <= 0:
            return "pending", f"Модель {camera} ещё не обработала ни одного кадра"
    return "synced", ""


def contour_readiness(
    current: dict,
    desired: list[str],
    analytics_scope: str,
) -> dict[str, dict[str, str]]:
    """Return per-camera readiness without coupling the two contours."""

    result = {}
    for camera in desired:
        status, detail = _processor_readiness(
            current,
            camera,
            analytics_scope=analytics_scope,
        )
        result[camera] = {"status": status, "detail": detail}
    return result


def contour_sync_state(
    current: dict,
    desired: list[str],
    analytics_scope: str,
) -> tuple[str, str]:
    readiness = contour_readiness(current, desired, analytics_scope)
    pending = [
        f"{camera}: {value['detail']}"
        for camera, value in readiness.items()
        if value["status"] != "synced"
    ]
    return ("pending", "; ".join(pending)) if pending else ("synced", "")


def contour_state(
    desired: list[str],
    analytics_scope: str,
) -> tuple[dict | None, str, str]:
    """Cached camera-PC status of one contour: ``(live, sync_status, detail)``.

    ``live`` is ``None`` when the AI service is off or unreachable.
    """

    if not ai.enabled():
        return None, "pending", _AI_NOT_CONFIGURED
    try:
        live = ai.always_on_status_cached()
    except (ai.AiUnavailable, ai.AiError) as exc:
        return None, "pending", str(exc)
    return (live, *contour_sync_state(live, desired, analytics_scope))


def _record_counts(cameras: list[str]) -> None:
    """Import the durable count journal of every listed camera."""

    for camera, result in _camera_event_results(cameras):
        if result is None:
            continue
        if result.processed or result.ignored or not result.caught_up:
            log.info(
                "Camera events synchronized camera=%s processed=%s ignored=%s "
                "cursor=%s pages=%s caught_up=%s",
                camera,
                result.processed,
                result.ignored,
                result.last_event_id,
                result.pages,
                result.caught_up,
            )


def _sync_camera_events(
    camera: str, *, threaded: bool = False
) -> event_sync.SyncResult | None:
    try:
        if threaded:
            close_old_connections()
        try:
            return event_sync.sync_camera(camera)
        except (ai.AiUnavailable, ai.AiError, event_sync.EventSyncError) as exc:
            # An uncertain journal is a recorded failure; the next poll
            # resumes from the committed cursor.
            log.warning("Camera event sync failed camera=%s: %s", camera, exc)
            event_sync.mark_sync_failure(camera, exc)
            return None
        except Exception as exc:
            event_sync.mark_sync_failure(camera, exc)
            raise
    finally:
        if threaded:
            # Executor threads have their own Django connections, outside the
            # HTTP request lifecycle. Release them even after failed imports.
            connections.close_all()


def _camera_event_results(
    cameras: list[str],
) -> Iterator[tuple[str, event_sync.SyncResult | None]]:
    cameras = sorted({ai.normalize(camera) for camera in cameras})
    workers = min(settings.CAMERA_EVENT_SYNC_WORKERS, len(cameras))
    if workers <= 1:
        for camera in cameras:
            yield camera, _sync_camera_events(camera)
        return
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="camera-events"
    ) as pool:
        pending = {
            pool.submit(_sync_camera_events, camera, threaded=True): camera
            for camera in cameras
        }
        failures: list[Exception] = []
        for future in as_completed(pending):
            camera = pending[future]
            try:
                result = future.result()
            except Exception as exc:
                # Finish healthy cameras before surfacing an unexpected fault;
                # don't convert a programming/DB error into a successful sync.
                log.exception("Camera event worker failed camera=%s", camera)
                failures.append(exc)
            else:
                yield camera, result
        if failures:
            raise failures[0]


def sync_always_on_policy(
    *,
    previous_sources: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Apply PostgreSQL's effective monoblock policy to the camera PC.

    Callers persist their business change before entering this function.  A
    network failure therefore leaves one durable desired state which the
    monitor can reconcile later, rather than rolling PostgreSQL back after the
    camera PC may already have accepted the request.
    """

    # Serialize camera-PC policy writes across threads and web workers.
    with _ALWAYS_ON_LOCAL_MUTEX, advisory_lock(
        _ALWAYS_ON_ADVISORY_NAMESPACE, _ALWAYS_ON_ADVISORY_KEY
    ):
        # Read the durable policy only after acquiring the cross-worker mutex.
        # Requests that reached the camera PC out of HTTP order therefore all
        # apply the newest committed PostgreSQL state, never a stale snapshot.
        desired = MonoblockCameraSettings.continuous_sources()
        analytics_scopes = MonoblockCameraSettings.continuous_roles()
        desired_set = set(desired)
        removed_sources = set(previous_sources or []) - desired_set
        for camera in removed_sources:
            event_sync.request_stop_drain(camera)

        current = ai.configure_always_on(
            desired,
            "sub",
            analytics_scopes=analytics_scopes,
        )
        for camera in removed_sources:
            event_sync.confirm_stop_drain(camera)
        return current


def apply_always_on_policy(
    previous_sources: list[str],
    analytics_scope: str,
) -> tuple[dict | None, str, str]:
    """Best-effort immediate apply; PostgreSQL remains the durable authority.

    Returns ``(live, sync_status, detail)`` of one contour like
    :func:`contour_state`; the monitor reconciles anything left pending.
    """

    if not ai.enabled():
        return None, "pending", _AI_NOT_CONFIGURED
    try:
        live = sync_always_on_policy(previous_sources=previous_sources)
    except (ai.AiUnavailable, ai.AiError) as exc:
        return None, "pending", str(exc)
    desired = MonoblockCameraSettings.contour_sources(analytics_scope)
    return (live, *contour_sync_state(live, desired, analytics_scope))


def reconcile() -> dict:
    """Make the camera-PC durable state match PostgreSQL's desired state."""
    desired = MonoblockCameraSettings.continuous_sources()
    analytics_scopes = MonoblockCameraSettings.continuous_roles()
    current = ai.always_on_status()
    normalized_current_sources = _normalized_live_sources(current)
    current_source = current.get("source", "sub")
    if normalized_current_sources is None:
        # Ответ без разборного списка камер — это «состояние неизвестно», а не
        # «камер нет». Раньше он приводился к [] и, если в PostgreSQL тоже было
        # пусто, расхождения не возникало — зато при непустом выборе монитор
        # честно перезаписывал ПК. Обратный случай опаснее: считать выбор
        # применённым нельзя, но и продавливать что-либо по неизвестному
        # состоянию мы не будем — просто ждём следующей итерации.
        log.warning(
            "Камера-ПК вернул always-on без списка камер (%r) — "
            "состояние неизвестно, синхронизация отложена",
            current.get("camera_sources"),
        )
        _record_counts(
            sorted(set(desired) | AlwaysOnCounterCursor.draining_cameras())
        )
        return current
    desired_sources = set(desired)
    removed_sources = normalized_current_sources - desired_sources
    current_scopes = current.get("analytics_scopes")
    policy_changed = (
        normalized_current_sources != desired_sources
        or current_source != "sub"
        or current_scopes != analytics_scopes
    )
    if policy_changed:
        try:
            current = sync_always_on_policy(
                previous_sources=sorted(normalized_current_sources),
            )
        except (ai.AiUnavailable, ai.AiError):
            # A broken new mapping must not starve the event journal of an
            # already healthy processor. Import the observed live set first,
            # then let the monitor report/retry the policy failure.
            _record_counts(
                sorted(
                    normalized_current_sources
                    | desired_sources
                    | AlwaysOnCounterCursor.draining_cameras()
                )
            )
            raise
        desired_sources = set(MonoblockCameraSettings.continuous_sources())
        normalized_current_sources = _normalized_live_sources(current) or set()
    unconfirmed_stop_sources = set(
        AlwaysOnCounterCursor.objects.filter(
            event_stop_drain_requested_at__isnull=False,
            event_stop_confirmed_at__isnull=True,
        ).values_list("camera", flat=True)
    )
    stopped_pending_sources = (
        unconfirmed_stop_sources - normalized_current_sources - desired_sources
    )
    for camera in stopped_pending_sources:
        # Recovery after a process crash between the remote stop response and
        # its second durable barrier: the live configuration itself confirms
        # that this camera is now stopped.
        event_sync.confirm_stop_drain(camera)
    dangling_reactivations = (
        unconfirmed_stop_sources & normalized_current_sources & desired_sources
    )
    if dangling_reactivations:
        reactivation_fence = timezone.now()
        for camera in sorted(dangling_reactivations):
            event_sync.reactivate_stop_drain(
                camera,
                required_at=reactivation_fence,
            )
    draining_sources = AlwaysOnCounterCursor.draining_cameras()
    _record_counts(sorted(desired_sources | removed_sources | draining_sources))
    return current


# Как часто спрашиваем камеру про табличку вагона. Состав стоит под разгрузкой
# долго, поэтому минуты достаточно: чаще — лишняя нагрузка на модель, реже —
# заметная задержка появления рейса на экране.
WAGON_PLATE_PERIOD = timedelta(minutes=1)
WAGON_PLATE_STATE_KEY = "cameras:wagon-plate-last-poll:v1"


def poll_wagon_plate() -> dict:
    """Открыть приход, когда камера видит табличку вагона.

    Заменяет отсутствующий датчик прибытия. Если OCR сам подтвердил номер,
    рейс связывается с ожидаемым вагоном; иначе создаётся без номера, чтобы
    оператор мог безопасно заполнить его вручную.

    Опрос идёт реже цикла мониторинга: тот крутится каждые 30 секунд, а
    спрашивать модель чаще раза в минуту незачем.
    """
    camera = MonoblockCameraSettings.wagon_number_source()
    if not camera:
        return {"skipped": "no_camera"}

    last = cache.get(WAGON_PLATE_STATE_KEY)
    now = timezone.now()
    if last and now - last < WAGON_PLATE_PERIOD:
        return {"skipped": "too_soon"}
    cache.set(WAGON_PLATE_STATE_KEY, now, int(WAGON_PLATE_PERIOD.total_seconds()) * 4)

    scan = ai.wagon_plate_scan(camera)
    if scan is None:
        # Нет кадра или сервис молчит. Это «неизвестно», а не «поезда нет»:
        # молча закрывать по такому ответу ничего нельзя.
        return {"seen": None}
    if not scan["seen"]:
        return {"seen": False}

    number = scan.get("number") or ""
    wagon = grain_services.register_detected_arrival(
        camera_source=camera,
        number=number,
    )
    if wagon is None:
        # Тот же состав всё ещё под камерой — рейс уже заведён.
        return {"seen": True, "number": number, "created": None}
    log.info(
        "Камера %s зафиксировала прибытие состава: рейс #%s, вагон %s",
        camera,
        wagon.pk,
        wagon.number or "не распознан",
    )
    return {"seen": True, "number": number, "created": wagon.pk}
