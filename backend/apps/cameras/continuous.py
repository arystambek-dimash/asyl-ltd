from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import timedelta

from django.core.cache import cache
from django.db import DatabaseError, connection
from django.db.models import Q
from django.utils import timezone

from apps.grain import services as grain_services

from . import ai, analytics, event_sync
from .models import AlwaysOnCounterCursor, MonoblockCameraSettings

log = logging.getLogger(__name__)

_ALWAYS_ON_LOCAL_MUTEX = threading.RLock()
_ALWAYS_ON_ADVISORY_NAMESPACE = 0x4149  # "AI"
_ALWAYS_ON_ADVISORY_KEY = 0x323437  # "247"


@contextmanager
def _always_on_policy_mutex():
    """Serialize camera-PC policy writes across threads and web workers."""

    with _ALWAYS_ON_LOCAL_MUTEX:
        if connection.vendor != "postgresql":
            yield
            return

        acquired = False
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_lock(%s, %s)",
                    [_ALWAYS_ON_ADVISORY_NAMESPACE, _ALWAYS_ON_ADVISORY_KEY],
                )
            acquired = True
            yield
        finally:
            if acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(%s, %s)",
                            [_ALWAYS_ON_ADVISORY_NAMESPACE, _ALWAYS_ON_ADVISORY_KEY],
                        )
                        released = cursor.fetchone()
                    if released != (True,):
                        log.error("PostgreSQL always-on policy lock was not owned")
                except DatabaseError:
                    log.exception("Could not release PostgreSQL always-on policy lock")
                    connection.close()


def always_on_sync_state(current: dict, desired: list[str]) -> tuple[str, str]:
    """Return a fail-closed readiness state for one camera-PC policy reply."""

    live_sources = current.get("cameras")
    if not isinstance(live_sources, list):
        live_sources = current.get("camera_sources")
    if not isinstance(live_sources, list):
        return "pending", "AI-сервис не подтвердил список камер AI 24/7"
    if any(not isinstance(source, str) for source in live_sources):
        return "pending", "AI-сервис вернул некорректный список камер AI 24/7"
    if sorted(live_sources) != sorted(desired) or current.get("source") != "sub":
        return "pending", "Настройка AI 24/7 ожидает синхронизации"

    upstream_pending = current.get("pending")
    if upstream_pending:
        pending_rows = upstream_pending if isinstance(upstream_pending, list) else []
        reasons = []
        for item in pending_rows:
            if not isinstance(item, dict):
                continue
            camera = item.get("cam")
            reason = item.get("reason")
            if isinstance(camera, str) and isinstance(reason, str):
                reasons.append(f"{camera}: {reason}")
        detail = ", ".join(reasons) or "камера-ПК ещё запускает процессоры"
        return "pending", f"AI 24/7 ожидает готовности: {detail}"

    processors = current.get("processors")
    if not isinstance(processors, list):
        return "pending", "AI-сервис не подтвердил готовность процессоров"
    by_camera = {
        item.get("cam"): item
        for item in processors or []
        if isinstance(item, dict) and isinstance(item.get("cam"), str)
    }
    for camera in desired:
        processor = by_camera.get(camera)
        if (
            processor is None
            or processor.get("running") is not True
            or processor.get("processor_alive") is not True
        ):
            return "pending", f"AI 24/7 ожидает запуска процессора {camera}"
        if processor.get("source") != "sub":
            return "pending", f"Процессор {camera} ещё не перешёл на источник sub"
        mode = processor.get("mode")
        if mode not in {"always_on", "session"}:
            return "pending", f"AI 24/7 не подтвердил режим процессора {camera}"
        if mode == "session" and processor.get("continuous_analytics") is not True:
            return (
                "pending",
                f"Сессия {camera} не подключена к непрерывной аналитике AI 24/7",
            )
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
            return "pending", f"Камера {camera} ещё не передала кадр в AI 24/7"
        if isinstance(metrics, dict) and "inference_frames" in metrics:
            inference_frames = metrics.get("inference_frames")
            if type(inference_frames) is not int or inference_frames <= 0:
                return (
                    "pending",
                    f"Модель {camera} ещё не обработала ни одного кадра",
                )
    return "synced", ""


def _draining_event_sources() -> set[str]:
    return set(
        AlwaysOnCounterCursor.objects.exclude(event_sync_supported=False)
        .filter(
            Q(event_drain_required_at__isnull=False)
            | Q(event_stop_drain_requested_at__isnull=False)
            | ~Q(event_sync_error="")
            | Q(
                last_event_id__isnull=False,
                event_caught_up_at__isnull=True,
            )
        )
        .values_list("camera", flat=True)
    )


def _record_counts(current: dict, desired: list[str]) -> None:
    """Use durable events when supported and snapshots only for explicit 404s."""

    legacy_snapshot_cameras: set[str] = set()
    for camera in desired:
        try:
            result = event_sync.sync_camera(camera)
        except (ai.AiUnavailable, ai.AiError, event_sync.EventSyncError) as exc:
            # An uncertain journal is never permission to use the aggregate
            # snapshot: the next successful page would then count it twice.
            log.warning("Camera event sync failed camera=%s: %s", camera, exc)
            event_sync.mark_sync_failure(camera, exc)
            continue
        if not result.supported:
            legacy_snapshot_cameras.add(camera)
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

    if legacy_snapshot_cameras:
        analytics.record_snapshot(current, cameras=legacy_snapshot_cameras)


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

    with _always_on_policy_mutex():
        # Read the durable policy only after acquiring the cross-worker mutex.
        # Requests that reached the camera PC out of HTTP order therefore all
        # apply the newest committed PostgreSQL state, never a stale snapshot.
        desired = MonoblockCameraSettings.always_on_sources()
        desired_set = set(desired)
        removed_sources = set(previous_sources or []) - desired_set
        for camera in removed_sources:
            event_sync.request_stop_drain(camera)

        current = ai.configure_always_on(desired, "sub")
        for camera in removed_sources:
            event_sync.confirm_stop_drain(camera)
        return current


def reconcile() -> dict:
    """Make the camera-PC durable state match PostgreSQL's desired state."""
    desired = sorted(MonoblockCameraSettings.always_on_sources())
    current = ai.always_on_status()
    current_sources = current.get("cameras")
    current_source = current.get("source", "sub")
    if not isinstance(current_sources, list):
        # Ответ без разборного списка камер — это «состояние неизвестно», а не
        # «камер нет». Раньше он приводился к [] и, если в PostgreSQL тоже было
        # пусто, расхождения не возникало — зато при непустом выборе монитор
        # честно перезаписывал ПК. Обратный случай опаснее: считать выбор
        # применённым нельзя, но и продавливать что-либо по неизвестному
        # состоянию мы не будем — просто ждём следующей итерации.
        log.warning(
            "Камера-ПК вернул always-on без списка камер (%r) — "
            "состояние неизвестно, синхронизация отложена",
            current_sources,
        )
        _record_counts(current, sorted(set(desired) | _draining_event_sources()))
        return current
    normalized_current_sources: set[str] = set()
    for source in current_sources:
        try:
            normalized_current_sources.add(ai.normalize(source))
        except ai.AiError:
            continue
    desired_sources = set(desired)
    removed_sources = normalized_current_sources - desired_sources
    if normalized_current_sources != desired_sources or current_source != "sub":
        current = sync_always_on_policy(
            previous_sources=sorted(normalized_current_sources),
        )
        desired_sources = set(MonoblockCameraSettings.always_on_sources())
        configured_sources = current.get("cameras")
        if not isinstance(configured_sources, list):
            configured_sources = current.get("camera_sources")
        normalized_current_sources = set()
        for source in configured_sources or []:
            try:
                normalized_current_sources.add(ai.normalize(source))
            except ai.AiError:
                continue
    stopped_pending_sources = set(
        AlwaysOnCounterCursor.objects.filter(
            event_stop_drain_requested_at__isnull=False,
            event_stop_confirmed_at__isnull=True,
        ).values_list("camera", flat=True)
    ) - normalized_current_sources - desired_sources
    for camera in stopped_pending_sources:
        # Recovery after a process crash between the remote stop response and
        # its second durable barrier: the live configuration itself confirms
        # that this camera is now stopped.
        event_sync.confirm_stop_drain(camera)
    draining_sources = _draining_event_sources()
    _record_counts(
        current,
        sorted(desired_sources | removed_sources | draining_sources),
    )
    return current


def reconcile_wagon_number() -> dict:
    """Keep the durable wagon-number camera role in sync after restarts."""
    desired = MonoblockCameraSettings.wagon_number_source() or None
    current = ai.wagon_number_status()
    if current.get("camera") != desired or current.get("source") != "main":
        current = ai.configure_wagon_number(desired, "main")
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
