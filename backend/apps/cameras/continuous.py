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
from .models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCounterCursor,
    MonoblockCameraSettings,
)

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


def _live_sources(current: dict) -> list[str] | None:
    live_sources = current.get("cameras")
    if not isinstance(live_sources, list):
        live_sources = current.get("camera_sources")
    if not isinstance(live_sources, list) or any(
        not isinstance(source, str) for source in live_sources
    ):
        return None
    return live_sources


def _processor_readiness(
    current: dict,
    camera: str,
    *,
    analytics_scope: str | None,
) -> tuple[str, str]:
    live_sources = _live_sources(current)
    if live_sources is None or camera not in live_sources:
        return "pending", f"Камера {camera} ещё не применена на AI-сервисе"
    if current.get("source") != "sub":
        return "pending", f"Камера {camera} ещё не перешла на источник sub"

    if analytics_scope is not None:
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
    if analytics_scope is not None and processor.get("analytics_scope") != analytics_scope:
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


def policy_sync_state(
    current: dict,
    desired: list[str],
    analytics_scopes: dict[str, str],
) -> tuple[str, str]:
    """Validate the complete role-aware camera-PC control-plane reply."""

    live_sources = _live_sources(current)
    if live_sources is None:
        return "pending", "AI-сервис не подтвердил список непрерывных камер"
    if sorted(live_sources) != sorted(desired) or current.get("source") != "sub":
        return "pending", "Настройка непрерывных камер ожидает синхронизации"
    if current.get("analytics_scopes") != analytics_scopes:
        return "pending", "AI-сервис не подтвердил роли непрерывных камер"
    for camera in desired:
        state, detail = _processor_readiness(
            current,
            camera,
            analytics_scope=analytics_scopes[camera],
        )
        if state != "synced":
            return state, detail
    return "synced", ""


def always_on_sync_state(current: dict, desired: list[str]) -> tuple[str, str]:
    """Backward-compatible exact readiness check without role inference."""

    live_sources = _live_sources(current)
    if live_sources is None:
        return "pending", "AI-сервис не подтвердил список камер AI 24/7"
    if sorted(live_sources) != sorted(desired) or current.get("source") != "sub":
        return "pending", "Настройка AI 24/7 ожидает синхронизации"
    for camera in desired:
        state, detail = _processor_readiness(
            current,
            camera,
            analytics_scope=None,
        )
        if state != "synced":
            return state, detail
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


def _record_counts(
    current: dict,
    desired: list[str],
    analytics_scopes: dict[str, str],
) -> None:
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
        analytics.record_snapshot(
            current,
            cameras=legacy_snapshot_cameras,
            analytics_scopes=analytics_scopes,
        )


def _observed_analytics_scopes(
    current: dict,
    desired_scopes: dict[str, str],
    *,
    fallback_to_desired: bool = True,
) -> dict[str, str]:
    """Prefer the role attached to the processor that produced a snapshot."""

    result = dict(desired_scopes) if fallback_to_desired else {}
    live_scopes = current.get("analytics_scopes")
    if isinstance(live_scopes, dict):
        for camera, scope in live_scopes.items():
            if (
                isinstance(camera, str)
                and scope in {ANALYTICS_SCOPE_SHIPPING, ANALYTICS_SCOPE_AI247}
            ):
                result[camera] = scope
    return result


def _confirmed_shipping_sources(
    current: dict,
    desired_sources: set[str],
) -> list[str]:
    """Return desired live cameras whose role CV explicitly acknowledged."""

    live_sources = _live_sources(current)
    scopes = current.get("analytics_scopes")
    if live_sources is None or not isinstance(scopes, dict):
        return []
    normalized_live = set()
    for source in live_sources:
        try:
            normalized_live.add(ai.normalize(source))
        except ai.AiError:
            continue
    return sorted(
        camera
        for camera in desired_sources & normalized_live
        if scopes.get(camera) == ANALYTICS_SCOPE_SHIPPING
        and _processor_readiness(
            current,
            camera,
            analytics_scope=ANALYTICS_SCOPE_SHIPPING,
        )[0]
        == "synced"
    )


def _confirm_shipping_bootstraps(cameras: list[str]) -> None:
    for camera in cameras:
        analytics.confirm_shipping_bootstrap_scope(camera)


def _complete_shipping_bootstraps(cameras: list[str]) -> None:
    for camera in cameras:
        analytics.complete_shipping_bootstrap(camera)


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


def reconcile() -> dict:
    """Make the camera-PC durable state match PostgreSQL's desired state."""
    desired = MonoblockCameraSettings.continuous_sources()
    analytics_scopes = MonoblockCameraSettings.continuous_roles()
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
        _record_counts(
            current,
            sorted(set(desired) | _draining_event_sources()),
            _observed_analytics_scopes(current, analytics_scopes),
        )
        return current
    normalized_current_sources: set[str] = set()
    for source in current_sources:
        try:
            normalized_current_sources.add(ai.normalize(source))
        except ai.AiError:
            continue
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
            confirmed_shipping = _confirmed_shipping_sources(
                current,
                desired_sources,
            )
            _confirm_shipping_bootstraps(confirmed_shipping)
            _record_counts(
                current,
                sorted(
                    normalized_current_sources
                    | desired_sources
                    | _draining_event_sources()
                ),
                _observed_analytics_scopes(
                    current,
                    analytics_scopes,
                    fallback_to_desired=False,
                ),
            )
            _complete_shipping_bootstraps(confirmed_shipping)
            raise
        desired_sources = set(MonoblockCameraSettings.continuous_sources())
        analytics_scopes = MonoblockCameraSettings.continuous_roles()
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
    active_desired_sources = normalized_current_sources & desired_sources
    dangling_reactivations = set(
        AlwaysOnCounterCursor.objects.filter(
            camera__in=active_desired_sources,
            event_stop_drain_requested_at__isnull=False,
            event_stop_confirmed_at__isnull=True,
        ).values_list("camera", flat=True)
    )
    if dangling_reactivations:
        reactivation_fence = timezone.now()
        for camera in sorted(dangling_reactivations):
            event_sync.reactivate_stop_drain(
                camera,
                required_at=reactivation_fence,
            )
    confirmed_shipping = _confirmed_shipping_sources(current, desired_sources)
    _confirm_shipping_bootstraps(confirmed_shipping)
    draining_sources = _draining_event_sources()
    _record_counts(
        current,
        sorted(desired_sources | removed_sources | draining_sources),
        _observed_analytics_scopes(current, analytics_scopes),
    )
    _complete_shipping_bootstraps(confirmed_shipping)
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
