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
    begin_camera_loading,
    ensure_scale_entry_weight,
    finish_ai_loading,
    read_scale_exit_if_required,
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


def _payload(value: object) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _valid_total(value: object) -> int | None:
    if type(value) is not int or not 0 <= value <= MAX_COUNTER_TOTAL:
        return None
    return value


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


def _finish_pending_cleanup(
    camera: str,
    exclude_session_id: int | None = None,
    *,
    known_session_worker: bool = False,
) -> None:
    """Idle an orphaned worker before it can leak its count into a new order."""
    pending = _pending_cleanup(camera, exclude_session_id)
    if pending is None:
        return
    should_delete = known_session_worker
    if not known_session_worker:
        live = ai.status(camera)
        live_payload = _payload(live)
        should_delete = (
            live is not None
            and live_payload.get("running") is True
            and live_payload.get("mode") != "always_on"
        )
    if should_delete:
        # Only a per-order worker is ours. A restored always-on processor must
        # never be reset while resolving an old cleanup marker.
        ai.delete(camera)
    # One successful idle command resolves every older marker for this camera.
    resolved = AiCountingSession.objects.filter(
        camera=camera,
        error__startswith=CLEANUP_PENDING_PREFIX,
    )
    if exclude_session_id is not None:
        resolved = resolved.exclude(pk=exclude_session_id)
    resolved.update(error="")


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
    worker_running = live is not None and live_payload.get("running") is True
    is_session_worker = worker_running and live_payload.get("mode") != "always_on"
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
        order.status in ("arrived", "loading") and order.loading_camera == camera
    )
    if order.status != "confirmed" and not restoring_same_binding:
        raise ai.AiError(
            400,
            "Для новой отгрузки выберите заказ в статусе «Ожидание въезда»",
        )
    if camera not in MonoblockCameraSettings.allowed_sources():
        raise ai.AiError(
            400,
            "Эта камера не разрешена администратором для Моноблока",
        )


def _release_unweighed_reservation(session: AiCountingSession) -> None:
    """Remove only our provisional reservation when scale capture failed.

    A concurrent retry may have stored the entry while this request was
    waiting for the scale. In that case the reservation is no longer orphaned
    and must be left for that retry to activate.
    """
    from apps.shipments.models import Shipment

    with transaction.atomic():
        locked = AiCountingSession.objects.select_for_update().filter(
            pk=session.pk,
            status=AiCountingSession.STARTING,
        ).first()
        if locked is None:
            return
        captured = Shipment.objects.filter(
            order_id=locked.order_id,
            weigh_in_source=Shipment.WeightSource.SCALE,
            weigh_in_kg__isnull=False,
        ).exists()
        if not captured:
            locked.delete()


def start(camera: str, order: Order, user) -> dict:
    """Reserve a camera, start its worker, then begin the DB loading.

    The camera is reserved before the physical scale read, so a request that
    loses a camera race cannot leave an entry weight on the wrong workflow.
    The read itself stays outside DB transactions; its save rechecks both the
    reservation and truck number under row locks. The AI remote call still
    happens before ``begin_camera_loading``. An ambiguous AI timeout keeps the
    ``starting`` reservation; repeating this command reconciles it.
    """
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    # Cheap preflight first; reservation and order state are checked again
    # around every durable mutation below.
    _validate_start(order, camera)
    # Commit the reservation while holding the same device-row lock used by
    # configuration mutations. Once this short transaction commits, an admin
    # sees the OPEN session and cannot strand it by moving/deleting the device.
    with transaction.atomic():
        _lock_device_camera(user, camera)
        _validate_start(order, camera)
        session, created = sessions.reserve(order, camera, user)

    # No DB lock spans the scale HTTP request. Saving the sample locks the
    # provisional session first and the exact order second, and verifies that
    # both the reservation and truck number still match this request.
    if Order.objects.filter(pk=order.pk, status="confirmed").exists():
        try:
            ensure_scale_entry_weight(
                order,
                user,
                reservation_id=session.pk,
                camera=camera,
            )
        except Exception:
            if created:
                _release_unweighed_reservation(session)
            raise

    deterministic_error: ai.AiError | None = None
    validation_error: ValidationError | None = None

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
        # ``created`` was observed before this row lock. Another request may
        # have activated the same reservation while this one waited, so only
        # a still-STARTING row may run the one-time zeroing path.
        initialize_worker = created and was_starting
        worker_may_be_running = False
        try:
            if was_starting:
                _finish_pending_cleanup(camera, session.pk)
            if initialize_worker:
                live = ai.start(camera)
                worker_may_be_running = True
            else:
                live = ai.status(camera)
                live_payload = _payload(live)
                if (
                    live is None
                    or live_payload.get("running") is not True
                    or live_payload.get("mode") == "always_on"
                ):
                    live = ai.start(camera)
                worker_may_be_running = live is not None

            # POST /processors is idempotent and can attach a brand-new DB
            # reservation to an orphaned worker, so a newly created session
            # gets one explicit zero. A STARTING retry is different: the
            # earlier timed-out POST may have succeeded and already counted
            # real bags, therefore its running worker must be adopted without
            # resetting. If it is idle/always-on, ai.start above resets it.
            if initialize_worker:
                live = ai.reset(camera)
            live_payload = _payload(live)
            if live_payload.get("running") is not True:
                raise ai.AiError(503, "AI-сервис не подтвердил запуск счётчика")
        except ai.AiError as exc:
            # 4xx is a definitive refusal. Only a not-yet-activated session is
            # closed; an active session stays reserved on auth/config errors.
            if exc.status < 500 and was_starting:
                if worker_may_be_running:
                    try:
                        ai.delete(camera)
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
            except ValidationError as exc:
                # The worker did start, but the order changed concurrently.
                # Compensate remotely. If that is ambiguous, the durable
                # marker forces cleanup before any future order can start.
                try:
                    ai.delete(camera)
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
    is_session_worker = (
        live is not None
        and live_payload.get("running") is True
        and live_payload.get("mode") != "always_on"
    )
    if not is_session_worker:
        # After a camera-PC restart the configured 24/7 worker may be back on
        # this camera. Its total belongs to analytics, not to this order, and
        # DELETE would reset that unrelated counter.
        final = _stored_snapshot(session)
        return final, _valid_total(final.get("total")), False, None

    final = live_payload
    safe_total = _save_final_snapshot(session, final)
    return final, safe_total, True, None


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
) -> dict:
    """Close local ownership even when remote cleanup is temporarily broken.

    A failed DELETE is recorded on the closed session. Before a later order is
    allowed to start, :func:`start` retries that idempotent cleanup, preventing
    the old counter from leaking into the new loading.
    """
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    final: dict = {}
    cleanup_needed = False
    capture_failure: Exception | None = None
    cleanup_failure: Exception | None = None
    actual_bags: int | None = None
    session_id: int | None = None
    exit_reading = None

    # Внешний сервис может отвечать до нескольких секунд. Ни строка заказа,
    # ни AI-сессия в это время не заблокированы; транзакция ниже повторно
    # валидирует их перед единой записью веса, склада и статуса.
    if complete_order and order.status == "loading":
        preflight_session = sessions.current_for_camera(camera)
        if preflight_session is not None:
            if preflight_session.order_id != order.pk:
                raise sessions.AiSessionBusy(preflight_session)
            if not can_control_session(preflight_session, user):
                raise PermissionDenied(
                    "Завершить отгрузку может только начавший её "
                    "сотрудник или администратор"
                )
            if preflight_session.status == AiCountingSession.ACTIVE:
                exit_reading = read_scale_exit_if_required(order)

    with transaction.atomic():
        session = _locked_open_session(camera)
        # A second concurrent stop re-evaluates the OPEN predicate after the
        # first transaction commits and reaches this idempotent branch.
        if session is None:
            response = {"running": False, **metadata(None, order.pk, camera, user)}
            if complete_order:
                order.refresh_from_db(fields=["status"])
                if order.status == "shipped":
                    response.update(
                        order_status="shipped",
                        bags_loaded=order.shipment.bags_loaded,
                    )
            return response
        if session.order_id != order.pk:
            raise sessions.AiSessionBusy(session)
        if not can_control_session(session, user):
            raise PermissionDenied(
                "Остановить отгрузку может только начавший её сотрудник или администратор"
            )
        session_id = session.pk

        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if complete_order and (
            session.status != AiCountingSession.ACTIVE
            or locked_order.status != "loading"
        ):
            raise ai.AiError(
                409,
                "Сначала восстановите запуск AI-счётчика или отмените его",
            )

        # Capture while ownership is locked, but do not stop the worker until
        # the order and final snapshot have committed. A business validation
        # error can then roll back safely without destroying the only live
        # copy of the count.
        final, safe_total, cleanup_needed, capture_failure = _capture_final(
            camera,
            session,
        )

        # finish_ai_loading locks the order and validates the transition.
        if complete_order:
            shipment = finish_ai_loading(
                locked_order,
                safe_total,
                user,
                exit_reading=exit_reading,
            )
            actual_bags = shipment.bags_loaded
            # If the worker total was unusable, the shipment service chooses
            # its audited ordered-quantity fallback. Persist that actual value
            # as the session's final total too.
            final_total = actual_bags
        else:
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

    # Cleanup is deliberately after the business commit. The durable marker
    # serializes this attempt with a new start; if the camera PC is offline it
    # remains for the next start to resolve before reusing the worker.
    if cleanup_needed:
        if capture_failure is not None:
            # Do not make the operator wait through the same outage twice.
            # The marker blocks camera reuse until a future start can verify
            # and clean the worker.
            cleanup_failure = capture_failure
        else:
            try:
                with transaction.atomic():
                    _finish_pending_cleanup(
                        camera,
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
        response.update(order_status="shipped", bags_loaded=actual_bags)
    return response


def reset(camera: str, order: Order, user) -> dict:
    """Reset an owned live counter while serializing against start/stop."""
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    with transaction.atomic():
        session = _locked_open_session(camera)
        if session is None or session.order_id != order.pk:
            if session is not None:
                raise sessions.AiSessionBusy(session)
            raise ai.AiError(409, "Активная AI-сессия не найдена")
        if not can_control_session(session, user):
            raise PermissionDenied(
                "Сбросить счётчик может только начавший отгрузку сотрудник или администратор"
            )
        live = ai.reset(camera)
        live_payload = _payload(live)
        _save_live_status_locked(session, live_payload)
    return {**live_payload, **metadata(session, order.pk, camera, user)}
