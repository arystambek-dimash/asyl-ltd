"""Order-bound workflow for the camera AI counter.

The Windows camera service owns the live counter, while PostgreSQL owns the
business state: which order reserved a camera and whether the loading was
completed.  Keeping that coordination here makes the HTTP views adapters
instead of a second, implicit state machine.

There is deliberately no reconciliation in :func:`get_status`.  Polling is a
read operation; recovering a stopped worker is an explicit call to
:func:`start`, protected by ``shipping.load`` in the view.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.orders.models import Order
from apps.shipments.services import (
    assert_device_camera_change,
    begin_camera_loading,
    finish_ai_counting,
)

from . import ai, sessions
from .models import AiCountingSession, MonoblockCameraSettings, MonoblockDevice
from .policies import (
    assert_device_camera,
    can_control_session,
    session_started_by_name,
)

log = logging.getLogger(__name__)

# ``PositiveIntegerField`` uses a signed 32-bit integer on the supported
# databases.  Validate untrusted worker data before it reaches either the AI
# session or Shipment model.
MAX_COUNTER_TOTAL = 2_147_483_647
CLEANUP_PENDING_PREFIX = "AI worker cleanup pending: "


def _lock_device_camera(user, camera: str) -> None:
    """Serialize a device start against admin reassignment/deactivation."""
    sessions.lock_camera_binding()
    device = (
        MonoblockDevice.objects.select_for_update()
        .filter(user_id=user.pk)
        .first()
    )
    if not type(user)._default_manager.filter(pk=user.pk, is_active=True).exists():
        raise PermissionDenied("Учётная запись отключена администратором")
    if device is None:
        return
    if not device.is_active:
        raise PermissionDenied("Этот моноблок отключён администратором")
    if device.camera_source != camera:
        raise PermissionDenied("Эта камера закреплена за другим моноблоком")


def metadata(
    session: AiCountingSession | None,
    order_id: int | None,
    camera: str,
    user=None,
) -> dict:
    """Stable ownership fields shared by status and mutation responses."""
    if session is None:
        return {
            "available": True,
            "busy": False,
            "owned_by_order": False,
        }
    owner = session.order_id == order_id and session.camera == camera
    return {
        "available": owner,
        "busy": not owner,
        "owned_by_order": owner,
        "session_id": session.pk,
        "session_order_id": session.order_id,
        "session_camera": session.camera,
        "session_started_at": session.started_at,
        "session_started_by_id": session.started_by_id,
        "session_started_by_name": session_started_by_name(session),
        "can_stop": can_control_session(session, user),
    }


def _assert_order_department_scope(order_id: int, user) -> Order:
    """Lock Order then Client and recheck ownership before edge effects."""
    from apps.orders.services import lock_live_order

    return lock_live_order(order_id, user)


def _payload(value: object) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _valid_total(value: object) -> int | None:
    if type(value) is not int or not 0 <= value <= MAX_COUNTER_TOTAL:
        return None
    return value


def _assert_expected_session(
    session: AiCountingSession | None,
    expected_session_id: int | None,
) -> None:
    if expected_session_id is not None and (
        session is None or session.pk != expected_session_id
    ):
        raise ai.AiError(
            409,
            "AI-сессия изменилась; обновите страницу перед повтором команды",
        )


def _stream(payload: Mapping) -> str:
    value = payload.get("stream")
    return value[:64] if isinstance(value, str) and value else ""


def _cleanup_error(exc: Exception) -> str:
    detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
    return f"{CLEANUP_PENDING_PREFIX}{detail}"[:500]


def _mark_failed_locked(
    session: AiCountingSession,
    message: str,
    *,
    cleanup_pending: bool = False,
) -> None:
    session.status = AiCountingSession.FAILED
    session.ended_at = timezone.now()
    session.error = (
        f"{CLEANUP_PENDING_PREFIX}{message}" if cleanup_pending else message
    )[:500]
    session.save(update_fields=["status", "ended_at", "error"])


def _activate_locked(session: AiCountingSession, payload: dict) -> None:
    session.status = AiCountingSession.ACTIVE
    session.activated_at = session.activated_at or timezone.now()
    session.last_status = payload
    stream = _stream(payload)
    if stream:
        session.recording_stream = stream
    session.error = ""
    session.save(
        update_fields=[
            "status",
            "activated_at",
            "recording_stream",
            "last_status",
            "error",
        ]
    )


def _save_live_status_locked(session: AiCountingSession, payload: dict) -> None:
    session.last_status = payload
    stream = _stream(payload)
    if stream:
        session.recording_stream = stream
    session.save(update_fields=["recording_stream", "last_status"])


def _release_camera_binding(order_id: int, camera: str) -> None:
    """Release only the matching active binding, never historical orders."""
    Order.objects.filter(
        pk=order_id,
        loading_camera=camera,
        status__in=("confirmed", "arrived", "loading"),
    ).update(loading_camera="")


def _pending_cleanup(
    camera: str,
    exclude_session_id: int | None = None,
) -> AiCountingSession | None:
    sessions_to_clean = (
        AiCountingSession.objects.select_for_update()
        .filter(camera=camera, error__startswith=CLEANUP_PENDING_PREFIX)
    )
    if exclude_session_id is not None:
        sessions_to_clean = sessions_to_clean.exclude(pk=exclude_session_id)
    return sessions_to_clean.order_by("-ended_at", "-pk").first()


def _delete_exact_session(
    camera: str,
    session_id: int,
    *,
    invalid_detail: str = (
        "AI-сервис не подтвердил очистку точной сессии; "
        "повторите после восстановления камеры"
    ),
) -> tuple[dict, dict]:
    """Delete only one proven worker session and return its frozen final.

    HTTP 404 is not proof that the durable camera-PC boundary disappeared: a
    crashed processor can be recreated from that boundary by the always-on
    reconciler.  Cleanup is complete only after the scoped DELETE response
    names this exact session in its authoritative final payload.
    """

    stopped = ai.delete(camera, session_id=session_id)
    payload = _payload(stopped)
    final = _payload(payload.get("final"))
    outer_session_id = payload.get("session_id")
    exact_outer_identity = outer_session_id is None or (
        type(outer_session_id) is int and outer_session_id == session_id
    )
    if (
        stopped is None
        or payload.get("ok") is not True
        or payload.get("stopped") is not True
        or payload.get("cam") != camera
        or not exact_outer_identity
        or type(final.get("session_id")) is not int
        or final.get("session_id") != session_id
        or final.get("cam") != camera
        or final.get("mode") != "session"
        or final.get("running") is not False
    ):
        raise ai.AiError(503, invalid_detail)
    return payload, final


def _finish_pending_cleanup(
    camera: str,
    exclude_session_id: int | None = None,
    *,
    cleanup_session_id: int | None = None,
    known_session_worker: bool = False,
) -> None:
    """Idle an orphaned worker before it can leak its count into a new order."""
    while True:
        if cleanup_session_id is not None:
            pending = (
                AiCountingSession.objects.select_for_update()
                .filter(
                    pk=cleanup_session_id,
                    camera=camera,
                    error__startswith=CLEANUP_PENDING_PREFIX,
                )
                .first()
            )
        else:
            pending = _pending_cleanup(camera, exclude_session_id)
        if pending is None:
            return
        if not known_session_worker:
            live = ai.status(camera)
            live_payload = _payload(live)
            if live is None:
                raise ai.AiError(
                    503,
                    "AI-процессор ещё не восстановлен; очистка сессии отложена",
                )
            worker_session_id = live_payload.get("session_id")
            if worker_session_id is not None and (
                type(worker_session_id) is not int
                or worker_session_id != pending.pk
            ):
                raise ai.AiError(
                    409,
                    "AI-счётчик принадлежит другой сессии; очистка отложена",
                )
            if live_payload.get("mode") == "session":
                ai.assert_order_session_identity(live_payload, pending.pk)

        # The scoped response is the proof even when the processor has already
        # returned to always-on mode and replays a previously frozen final.
        _delete_exact_session(camera, pending.pk)
        # Resolve only the identity that was actually inspected/finished. A
        # stale marker must never clear another session's cleanup obligation.
        AiCountingSession.objects.filter(
            pk=pending.pk,
            error__startswith=CLEANUP_PENDING_PREFIX,
        ).update(error="")
        if cleanup_session_id is not None:
            return


def get_status(camera: str, order_id: int | None, user) -> dict:
    """Read live and ownership state without changing PostgreSQL or the worker.

    A ``starting`` reservation or an active row whose worker disappeared is
    reported as not running.  The UI can then issue the normal POST start
    command, which performs reconciliation under mutation permissions.
    """
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    session = sessions.current_for_camera(camera)
    info = metadata(session, order_id, camera, user)

    # Never query or mutate a worker owned by a different order.
    if session is None or not info["owned_by_order"]:
        return {"running": False, **info}

    live = ai.status(camera)
    live_payload = _payload(live)
    if live is not None:
        mode = live_payload.get("mode")
        if live_payload.get("session_id") is not None or mode == "session":
            ai.assert_order_session_identity(live_payload, session.pk)
    worker_running = live is not None and live_payload.get("running") is True
    is_session_worker = (
        worker_running
        and live_payload.get("mode") == "session"
        and live_payload.get("continuous_analytics") is True
    )
    is_active = session.status == AiCountingSession.ACTIVE
    if not is_session_worker or not is_active:
        code = (
            "ai_reconciliation_required"
            if session.status == AiCountingSession.STARTING or worker_running
            else "ai_processor_stopped"
        )
        return {**live_payload, "running": False, **info, "code": code}
    return {**live_payload, **info}


def _validate_start(order: Order, camera: str) -> None:
    # Ownership conflicts are more useful than a generic status error.
    camera_session = sessions.current_for_camera(camera)
    if camera_session and camera_session.order_id != order.pk:
        raise sessions.AiSessionBusy(camera_session)
    order_session = sessions.current_for_order(order.pk)
    if order_session and order_session.camera != camera:
        raise sessions.AiSessionBusy(order_session)

    restoring_same_binding = (
        order.status == "loading" and order.loading_camera == camera
    )
    if order.status not in ("confirmed", "arrived") and not restoring_same_binding:
        raise ai.AiError(
            400,
            "Загрузку можно начать только для подтверждённого или прибывшего заказа",
        )
    if camera not in MonoblockCameraSettings.allowed_sources():
        raise ai.AiError(
            400,
            "Эта камера не разрешена администратором для Моноблока",
        )


def start(
    camera: str,
    order: Order,
    user,
    *,
    expected_session_id: int | None = None,
) -> dict:
    """Reserve a camera, start its worker, then begin the DB loading.

    The AI remote call happens before ``begin_camera_loading``. An ambiguous
    AI timeout keeps the ``starting`` reservation; repeating this command
    reconciles it.
    """
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    _validate_start(order, camera)

    with transaction.atomic():
        _lock_device_camera(user, camera)
        from apps.orders.services import lock_live_order

        order = lock_live_order(order, user)
        existing = sessions.current_for_camera(camera)
        _assert_expected_session(existing, expected_session_id)
        _validate_start(order, camera)
        assert_device_camera_change(order, camera, user)
        session, created = sessions.reserve(order, camera, user)
        _assert_expected_session(session, expected_session_id)

    deterministic_error: ai.AiError | None = None
    validation_error: ValidationError | PermissionDenied | None = None

    with transaction.atomic():
        session = (
            AiCountingSession.objects.select_for_update(of=("self",))
            .select_related("started_by", "order")
            .get(pk=session.pk)
        )
        if session.status not in AiCountingSession.OPEN_STATUSES:
            raise ai.AiError(409, "AI-сессия уже завершена")
        if not can_control_session(session, user):
            raise PermissionDenied(
                "Восстановить AI-счётчик может только начавший отгрузку "
                "сотрудник или администратор"
            )

        was_starting = session.status == AiCountingSession.STARTING
        initialize_worker = created and was_starting
        worker_may_be_running = False
        try:
            if was_starting:
                _finish_pending_cleanup(camera, exclude_session_id=session.pk)
            if initialize_worker:
                live = ai.start(
                    camera,
                    {
                        "source": "sub",
                        "session_id": session.pk,
                        "require_continuous": True,
                    },
                )
                worker_may_be_running = True
            else:
                live = ai.status(camera)
                live_payload = _payload(live)
                if (
                    live is None
                    or live_payload.get("running") is not True
                    or live_payload.get("mode") != "session"
                ):
                    live = ai.start(
                        camera,
                        {
                            "source": "sub",
                            "session_id": session.pk,
                            "require_continuous": True,
                        },
                    )
                worker_may_be_running = live is not None
            live = ai.wait_for_order_session(
                camera,
                live,
                expected_session_id=session.pk,
            )
            live_payload = _payload(live)
            if live_payload.get("running") is not True:
                raise ai.AiError(503, "AI-сервис не подтвердил запуск счётчика")
        except ai.AiError as exc:
            if exc.status < 500 and was_starting:
                if worker_may_be_running:
                    try:
                        _delete_exact_session(camera, session.pk)
                    except (ai.AiError, ai.AiUnavailable) as cleanup_exc:
                        _mark_failed_locked(
                            session,
                            getattr(cleanup_exc, "detail", str(cleanup_exc)),
                            cleanup_pending=True,
                        )
                    else:
                        _mark_failed_locked(session, exc.detail)
                else:
                    _mark_failed_locked(session, exc.detail)
                _release_camera_binding(session.order_id, camera)
                deterministic_error = exc
            else:
                raise
        else:
            try:
                order = begin_camera_loading(order, camera, user)
            except (ValidationError, PermissionDenied) as exc:
                try:
                    _delete_exact_session(camera, session.pk)
                except (ai.AiError, ai.AiUnavailable) as cleanup_exc:
                    _mark_failed_locked(
                        session,
                        getattr(cleanup_exc, "detail", str(cleanup_exc)),
                        cleanup_pending=True,
                    )
                else:
                    _mark_failed_locked(session, str(exc.detail))
                _release_camera_binding(session.order_id, camera)
                validation_error = exc
            else:
                _activate_locked(session, live_payload)

    if deterministic_error is not None:
        raise deterministic_error
    if validation_error is not None:
        raise validation_error
    return {**live_payload, **metadata(session, order.pk, camera, user)}



def _save_final_snapshot(session: AiCountingSession, payload: dict) -> int | None:
    """Write the final counter before DELETE can idle the remote worker."""
    safe_total = _valid_total(payload.get("total"))
    updates: dict[str, object] = {"last_status": payload}
    # Never erase a valid snapshot with a malformed response from a later
    # attempt. It may be the only final count left after the worker is idled.
    if safe_total is not None:
        updates["final_total"] = safe_total
    stream = _stream(payload)
    if stream:
        updates["recording_stream"] = stream
    AiCountingSession.objects.filter(
        pk=session.pk,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).update(**updates)
    if safe_total is not None:
        session.final_total = safe_total
        return safe_total
    session.refresh_from_db(fields=["final_total"])
    return session.final_total


def _stored_snapshot(session: AiCountingSession) -> dict:
    session.refresh_from_db(fields=["last_status", "final_total"])
    snapshot = _payload(session.last_status)
    # ``last_status`` is a UI checkpoint (often the zero returned by start),
    # not proof of a final count. Only ``final_total`` was captured by an
    # explicit stop and is safe when the worker is gone.
    if session.final_total is None:
        snapshot.pop("total", None)
    else:
        snapshot["total"] = session.final_total
    return snapshot


def _capture_final(
    camera: str, session: AiCountingSession
) -> tuple[dict, int | None, bool, Exception | None]:
    """Capture only this loading's total without stopping the worker yet."""
    try:
        live = ai.status(camera)
    except (ai.AiError, ai.AiUnavailable) as exc:
        # The request is ambiguous: a session worker can still be running, so
        # the closed row must retain a cleanup marker.
        final = _stored_snapshot(session)
        return final, _valid_total(final.get("total")), True, exc

    live_payload = _payload(live)
    if live is None:
        final = _stored_snapshot(session)
        failure = ai.AiError(
            503,
            "AI-процессор не найден; durable session требует очистки",
        )
        return final, _valid_total(final.get("total")), True, failure
    if live_payload.get("mode") == "session":
        ai.assert_order_session_identity(live_payload, session.pk)
    is_session_worker = (
        live is not None
        and live_payload.get("running") is True
        and live_payload.get("mode") == "session"
        and live_payload.get("continuous_analytics") is True
    )
    if not is_session_worker:
        # After a camera-PC restart the configured 24/7 worker may be back on
        # this camera. Its total belongs to analytics, not to this order. The
        # local row can close, but its durable boundary remains pending until a
        # scoped DELETE proves this exact session was finalized or absent.
        final = _stored_snapshot(session)
        return final, _valid_total(final.get("total")), True, None

    final = live_payload
    safe_total = _save_final_snapshot(session, final)
    return final, safe_total, True, None


def _finish_with_authoritative_final(
    camera: str,
    session: AiCountingSession,
) -> tuple[dict, int]:
    """Atomically freeze and validate the exact durable order result.

    A GET followed by DELETE has an unavoidable counting gap: a crossing can
    land between those requests.  The camera PC therefore freezes the session
    inside scoped DELETE and durably replays the same ``final`` for retries
    after an ambiguous response or a rolled-back business transaction.
    """

    _, final = _delete_exact_session(
        camera,
        session.pk,
        invalid_detail=(
            "AI-сервис не подтвердил точный финальный счёт; "
            "повторите завершение"
        ),
    )
    if final.get("continuous_analytics") is not True:
        raise ai.AiError(
            503,
            "AI-сервис не подтвердил точный финальный счёт; повторите завершение",
        )
    safe_total = _valid_total(final.get("total"))
    if safe_total is None:
        raise ai.AiError(
            503,
            "AI-сервис вернул некорректный финальный счёт; повторите завершение",
        )
    return final, safe_total


def _locked_open_session(camera: str) -> AiCountingSession | None:
    return (
        AiCountingSession.objects.select_for_update(of=("self",))
        .select_related("started_by", "order")
        .filter(camera=camera, status__in=AiCountingSession.OPEN_STATUSES)
        .order_by("started_at")
        .first()
    )


def stop(
    camera: str,
    order: Order,
    user,
    *,
    complete_order: bool = False,
    expected_session_id: int | None = None,
) -> dict:
    """Finish an order exactly, or cancel local ownership best-effort.

    Business completion requires the scoped durable DELETE result before its
    database commit. A plain cancel may still close locally when remote cleanup
    is temporarily broken; :func:`start` retries that marked cleanup before a
    later order can reuse the camera.
    """
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    final: dict = {}
    cleanup_needed = False
    capture_failure: Exception | None = None
    cleanup_failure: Exception | None = None
    actual_bags: int | None = None
    session_id: int | None = None

    with transaction.atomic():
        session = _locked_open_session(camera)
        if session is None:
            locked_order = _assert_order_department_scope(order.pk, user)
            completed_session = None
            if expected_session_id is not None:
                completed_session = (
                    AiCountingSession.objects.select_for_update(of=("self",))
                    .filter(
                        pk=expected_session_id,
                        camera=camera,
                        order_id=locked_order.pk,
                        status=AiCountingSession.CLOSED,
                    )
                    .first()
                )
                if not (
                    complete_order
                    and locked_order.status == "loaded"
                    and completed_session is not None
                ):
                    _assert_expected_session(None, expected_session_id)
            response = {
                "running": False,
                **metadata(None, locked_order.pk, camera, user),
            }
            if complete_order and locked_order.status == "loaded":
                response.update(
                    order_status="loaded",
                    bags_loaded=locked_order.shipment.bags_loaded,
                )
                if completed_session is not None:
                    response.update(
                        session_id=completed_session.pk,
                        total=completed_session.final_total,
                    )
            return response
        _assert_expected_session(session, expected_session_id)
        if session.order_id != order.pk:
            raise sessions.AiSessionBusy(session)
        locked_order = _assert_order_department_scope(session.order_id, user)
        if not can_control_session(session, user):
            raise PermissionDenied(
                "Остановить отгрузку может только начавший её сотрудник "
                "или администратор"
            )
        session_id = session.pk

        if complete_order and (
            session.status != AiCountingSession.ACTIVE
            or locked_order.status != "loading"
        ):
            raise ai.AiError(
                409,
                "Сначала восстановите запуск AI-счётчика или отмените его",
            )

        if complete_order:
            # DELETE is the linearization point for the order total.  It runs
            # before the business commit, and the camera PC durably replays
            # the same final if this transaction later rolls back.
            final, safe_total = _finish_with_authoritative_final(camera, session)
            cleanup_needed = False
            shipment = finish_ai_counting(
                locked_order,
                safe_total,
                user,
            )
            actual_bags = shipment.bags_loaded
            final_total = actual_bags
        else:
            final, safe_total, cleanup_needed, capture_failure = _capture_final(
                camera,
                session,
            )
            _release_camera_binding(locked_order.pk, camera)
            final_total = safe_total

        session.status = AiCountingSession.CLOSED
        session.closed_by = user
        session.ended_at = timezone.now()
        session.final_total = final_total
        session.last_status = final
        stream = _stream(final)
        if stream:
            session.recording_stream = stream
        if cleanup_needed:
            pending_reason = capture_failure or RuntimeError("cleanup scheduled")
            session.error = _cleanup_error(pending_reason)
        else:
            session.error = ""
        session.save(
            update_fields=[
                "status",
                "closed_by",
                "ended_at",
                "final_total",
                "last_status",
                "recording_stream",
                "error",
            ]
        )

    if cleanup_needed:
        if capture_failure is not None:
            cleanup_failure = capture_failure
        else:
            try:
                with transaction.atomic():
                    _finish_pending_cleanup(
                        camera,
                        cleanup_session_id=session_id,
                        known_session_worker=True,
                    )
            except (ai.AiError, ai.AiUnavailable) as exc:
                cleanup_failure = exc
                AiCountingSession.objects.filter(
                    pk=session_id,
                    error__startswith=CLEANUP_PENDING_PREFIX,
                ).update(error=_cleanup_error(exc))

    response = {
        **final,
        "running": False,
        "available": True,
        "busy": False,
        "owned_by_order": False,
    }
    if cleanup_failure is not None:
        response["cleanup_pending"] = True
        log.warning(
            "AI worker cleanup pending for camera=%s session=%s: %s",
            camera,
            session_id,
            cleanup_failure,
        )
    if complete_order:
        response.update(order_status="loaded", bags_loaded=actual_bags)
    return response


def reset(
    camera: str,
    order: Order,
    user,
    *,
    expected_session_id: int | None = None,
) -> dict:
    """Reset an owned live counter while serializing against start/stop."""
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    with transaction.atomic():
        session = _locked_open_session(camera)
        _assert_expected_session(session, expected_session_id)
        if session is None or session.order_id != order.pk:
            if session is not None:
                raise sessions.AiSessionBusy(session)
            raise ai.AiError(409, "Активная AI-сессия не найдена")
        _assert_order_department_scope(session.order_id, user)
        if not can_control_session(session, user):
            raise PermissionDenied(
                "Сбросить счётчик может только начавший отгрузку сотрудник "
                "или администратор"
            )
        live = ai.reset(camera, session.pk)
        live_payload = _payload(live)
        ai.assert_order_session_identity(live_payload, session.pk)
        if (
            live_payload.get("running") is not True
            or live_payload.get("mode") != "session"
            or live_payload.get("continuous_analytics") is not True
        ):
            raise ai.AiError(
                409,
                "AI-сервис не подтвердил сброс точной непрерывной сессии",
            )
        _save_live_status_locked(session, live_payload)
    return {**live_payload, **metadata(session, order.pk, camera, user)}
