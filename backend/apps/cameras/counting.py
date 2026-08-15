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

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.conveyors import services as cloud_conveyors
from apps.eventlog.services import log_event
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


def _same_session_id(value: object, expected: int) -> bool:
    return (type(value) is int and value == expected) or (
        isinstance(value, str) and value == str(expected)
    )


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


def _ordered_target(order: Order) -> int:
    """Freeze the exact number of bags the edge controller must stop at."""
    total = sum(order.items.values_list("quantity", flat=True))
    if not 0 <= total <= MAX_COUNTER_TOTAL:
        raise ai.AiError(400, "Количество мешков в заказе вне допустимого диапазона")
    return total


def _conveyor_status(session: AiCountingSession, payload: Mapping) -> dict:
    raw = payload.get("conveyor")
    conveyor = dict(raw) if isinstance(raw, Mapping) else {}
    configured = (
        conveyor.get("configured") is True
        or conveyor.get("enabled") is True
        or session.conveyor_enabled
    )
    # Once a physical controller has been accepted for this DB session, a
    # downgraded/malformed edge response may never turn safety checks off.
    conveyor["configured"] = configured
    conveyor["enabled"] = configured

    desired_values: list[int] = []
    for value in (conveyor.get("desired"), conveyor.get("commanded")):
        if type(value) is bool:
            desired_values.append(int(value))
        elif type(value) is int and value in (0, 1):
            desired_values.append(value)
    if type(conveyor.get("commanded_on")) is bool:
        desired_values.append(int(conveyor["commanded_on"]))
    conveyor["desired"] = (
        desired_values[0]
        if desired_values and all(item == desired_values[0] for item in desired_values)
        else None
    )

    feedback_values: list[int] = []
    value = conveyor.get("feedback")
    if type(value) is bool:
        feedback_values.append(int(value))
    elif type(value) is int and value in (0, 1):
        feedback_values.append(value)
    if type(conveyor.get("feedback_on")) is bool:
        feedback_values.append(int(conveyor["feedback_on"]))
    if conveyor.get("verified_off") is True:
        feedback_values.append(0)
    feedback_conflict = bool(
        feedback_values
        and any(item != feedback_values[0] for item in feedback_values)
    )
    conveyor["feedback"] = (
        feedback_values[0] if feedback_values and not feedback_conflict else None
    )
    if feedback_conflict:
        conveyor["feedback_conflict"] = True
    if "last_seen_at" not in conveyor and "last_contact_at" in conveyor:
        conveyor["last_seen_at"] = conveyor.get("last_contact_at")
    if not configured:
        conveyor["state"] = "unconfigured"
        conveyor["online"] = False
        conveyor["desired"] = 0
        conveyor["feedback"] = None
    else:
        conveyor.setdefault("state", "unknown")
        conveyor.setdefault("online", False)
    return conveyor


def _with_control(
    session: AiCountingSession,
    payload: Mapping | None,
) -> dict:
    """Add a stable CRM contract around versioned camera-PC responses."""
    result = _payload(payload)
    target = session.target_total
    total = _valid_total(result.get("total"))
    conveyor = _conveyor_status(session, result)
    state = conveyor.get("state")
    goal_reached = bool(
        (target > 0 and total is not None and total >= target)
        or state == "goal_reached"
        or conveyor.get("goal_reached") is True
    )
    result.update(
        target_total=target,
        remaining=max(0, target - (total or 0)),
        goal_reached=goal_reached,
        conveyor=conveyor,
    )
    return result


def _with_cloud_control(
    session: AiCountingSession,
    payload: Mapping | None,
) -> dict:
    result = _payload(payload)
    conveyor = cloud_conveyors.control_payload_for_session(session)
    result["conveyor"] = conveyor
    live_total = _valid_total(result.get("total"))
    cloud_total = conveyor.get("total")
    if live_total is None and type(cloud_total) is int:
        result["total"] = cloud_total
    return _with_control(session, result)


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
    conveyor = payload.get("conveyor")
    session.conveyor_enabled = session.conveyor_enabled or bool(
        isinstance(conveyor, Mapping)
        and (
            conveyor.get("configured") is True
            or conveyor.get("enabled") is True
        )
    )
    session.error = ""
    session.save(
        update_fields=[
            "status",
            "activated_at",
            "recording_stream",
            "last_status",
            "conveyor_enabled",
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
    if session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD:
        live_payload = _with_cloud_control(session, live_payload)
    worker_running = live is not None and live_payload.get("running") is True
    is_session_worker = worker_running and live_payload.get("mode") != "always_on"
    is_active = session.status == AiCountingSession.ACTIVE
    if not is_session_worker or not is_active:
        code = (
            "ai_reconciliation_required"
            if session.status == AiCountingSession.STARTING or worker_running
            else "ai_processor_stopped"
        )
        return {
            **_with_control(session, live_payload),
            "running": False,
            **info,
            "code": code,
        }
    return {**_with_control(session, live_payload), **info}


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


def _cloud_ai_error(exc: cloud_conveyors.ConveyorDeviceError) -> ai.AiError:
    return ai.AiError(exc.status, exc.detail)


def _fail_cloud_start(
    session: AiCountingSession,
    camera: str,
    error: Exception,
    *,
    worker_started: bool,
) -> None:
    cleanup_pending = False
    try:
        device = cloud_conveyors.cloud_device_for(camera)
        if device is not None:
            cloud_conveyors.emergency_stop(device, "session_setup_failed")
    except Exception:  # noqa: BLE001 - OFF remains lease-enforced
        log.exception(
            "Unable to persist cloud conveyor OFF camera=%s session=%s",
            camera,
            session.pk,
        )
    if worker_started:
        try:
            ai.delete(camera)
        except (ai.AiError, ai.AiUnavailable):
            cleanup_pending = True
    with transaction.atomic():
        locked = AiCountingSession.objects.select_for_update().get(pk=session.pk)
        if locked.status == AiCountingSession.STARTING:
            _mark_failed_locked(
                locked,
                getattr(error, "detail", str(error)),
                cleanup_pending=cleanup_pending,
            )
            _release_camera_binding(locked.order_id, camera)


def _finish_pending_cleanup_cloud(camera: str, exclude_session_id: int) -> None:
    """Resolve an old worker without holding a DB lock during edge I/O."""
    pending = (
        AiCountingSession.objects.filter(
            camera=camera,
            error__startswith=CLEANUP_PENDING_PREFIX,
        )
        .exclude(pk=exclude_session_id)
        .order_by("-ended_at", "-pk")
        .first()
    )
    if pending is None:
        return
    ai.delete(camera)
    with transaction.atomic():
        AiCountingSession.objects.select_for_update().filter(
            camera=camera,
            error__startswith=CLEANUP_PENDING_PREFIX,
        ).exclude(pk=exclude_session_id).update(error="")


def _start_cloud(
    camera: str,
    order: Order,
    user,
    session: AiCountingSession,
    *,
    created: bool,
) -> dict:
    """Start cloud-leased hardware without holding DB locks while waiting."""
    if session.status == AiCountingSession.ACTIVE:
        live = _payload(ai.status(camera))
        device = cloud_conveyors.cloud_device_for(camera)
        if (
            device is None
            or device.command_session_id != session.pk
            or not device.desired_state
            or device.command_terminal
            or not cloud_conveyors.confirmed_state(device, True)
        ):
            raise ai.AiError(
                409,
                "Cloud conveyor session cannot be automatically resumed; "
                "perform a manual reconciliation",
            )
        return {
            **_with_cloud_control(session, live),
            **metadata(session, order.pk, camera, user),
        }

    worker_started = False
    try:
        _finish_pending_cleanup_cloud(camera, session.pk)
        cloud_conveyors.prepare_session(session)
        live, controlled = ai.start_order_session(
            camera,
            session.pk,
            session.target_total,
            initialize_legacy_worker=created,
            conveyor_transport="cloud",
        )
        worker_started = live is not None
        live_payload = _payload(live)
        if not controlled or live_payload.get("running") is not True:
            raise ai.AiError(
                503, "Camera PC did not bind the cloud conveyor session",
            )
        timeout = float(
            getattr(settings, "CONVEYOR_COMMAND_TIMEOUT_SECONDS", 5)
        )
        cloud_conveyors.wait_prepared(session, timeout)
    except cloud_conveyors.ConveyorDeviceError as exc:
        error = _cloud_ai_error(exc)
        _fail_cloud_start(
            session, camera, error, worker_started=worker_started,
        )
        raise error from exc
    except (ai.AiError, ai.AiUnavailable) as exc:
        _fail_cloud_start(
            session, camera, exc, worker_started=worker_started,
        )
        raise

    try:
        with transaction.atomic():
            locked = (
                AiCountingSession.objects.select_for_update(of=("self",))
                .select_related("order")
                .get(pk=session.pk)
            )
            if locked.status != AiCountingSession.STARTING:
                raise ai.AiError(409, "AI session changed during cloud preparation")
            if not can_control_session(locked, user):
                raise PermissionDenied(
                    "Only the employee who started loading or an administrator "
                    "may start this conveyor"
                )
            order = begin_camera_loading(
                locked.order,
                camera,
                user,
                reservation_id=locked.pk,
            )
            armed = cloud_conveyors.arm_session(locked)
            locked.conveyor_enabled = True
            live_payload["conveyor"] = cloud_conveyors.control_payload(armed)
            _activate_locked(locked, live_payload)
            session = locked
    except cloud_conveyors.ConveyorDeviceError as exc:
        error = _cloud_ai_error(exc)
        _fail_cloud_start(session, camera, error, worker_started=True)
        raise error from exc
    except (ai.AiError, ValidationError, PermissionDenied) as exc:
        _fail_cloud_start(session, camera, exc, worker_started=True)
        raise

    try:
        confirmed = cloud_conveyors.wait_confirmed(
            armed.pk,
            armed.command_revision,
            True,
            float(getattr(settings, "CONVEYOR_COMMAND_TIMEOUT_SECONDS", 5)),
        )
    except cloud_conveyors.ConveyorDeviceError as exc:
        stopped = cloud_conveyors.emergency_stop(armed, "start_timeout")
        try:
            cloud_conveyors.wait_confirmed(
                stopped.pk,
                stopped.command_revision,
                False,
                float(getattr(settings, "CONVEYOR_COMMAND_TIMEOUT_SECONDS", 5)),
            )
        except cloud_conveyors.ConveyorDeviceError as stop_exc:
            log.critical(
                "CRITICAL: cloud conveyor OFF not verified after start timeout "
                "camera=%s session=%s: %s",
                camera,
                session.pk,
                stop_exc,
            )
        raise _cloud_ai_error(exc) from exc

    live_payload["conveyor"] = cloud_conveyors.control_payload(confirmed)
    with transaction.atomic():
        current = _locked_open_session(camera)
        if current is not None and current.pk == session.pk:
            _save_live_status_locked(current, live_payload)
            session = current
    return {
        **_with_cloud_control(session, live_payload),
        **metadata(session, order.pk, camera, user),
    }


def start(
    camera: str,
    order: Order,
    user,
    *,
    expected_session_id: int | None = None,
) -> dict:
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
        # replace_items takes this same parent-row lock and refuses edits once
        # an OPEN session exists. The target therefore stays immutable from
        # reservation through the slow scale/edge calls and final loading
        # transition.
        order = Order.objects.select_for_update().get(pk=order.pk)
        existing = sessions.current_for_camera(camera)
        _assert_expected_session(existing, expected_session_id)
        _validate_start(order, camera)
        session, created = sessions.reserve(
            order,
            camera,
            user,
            target_total=_ordered_target(order),
            conveyor_transport=cloud_conveyors.transport_for(camera),
        )
        _assert_expected_session(session, expected_session_id)

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

    if session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD:
        return _start_cloud(
            camera,
            order,
            user,
            session,
            created=created,
        )

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
        edge_prepared = False
        try:
            if was_starting:
                _finish_pending_cleanup(camera, session.pk)
            if session.target_total > 0 and was_starting:
                live, edge_prepared = ai.start_order_session(
                    camera,
                    session.pk,
                    session.target_total,
                    initialize_legacy_worker=initialize_worker,
                )
                worker_may_be_running = live is not None
            elif session.target_total > 0 and session.conveyor_enabled:
                # An ACTIVE controlled session may only adopt the exact
                # in-memory edge binding. Recreating it after a camera-PC
                # reboot would reset total to zero and could overfill an order.
                live = ai.status(camera)
                worker_may_be_running = live is not None
            elif initialize_worker:
                # Legacy/test orders without line items can still use the AI
                # counter, but can never energize a target-controlled conveyor.
                live = ai.start(camera)
                worker_may_be_running = True
                live = ai.reset(camera)
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
            live_payload = _payload(live)
            if live_payload.get("running") is not True:
                raise ai.AiError(503, "AI-сервис не подтвердил запуск счётчика")
            if edge_prepared:
                try:
                    _assert_conveyor_prepared_off(session, live_payload)
                except ai.AiError:
                    try:
                        stopped_payload = _payload(
                            ai.emergency_stop_conveyor(camera, session.pk)
                        )
                        _assert_conveyor_physical_off(
                            session,
                            stopped_payload,
                        )
                    except (ai.AiError, ai.AiUnavailable) as stop_exc:
                        log.critical(
                            "CRITICAL: unsafe conveyor prepare response and OFF "
                            "was not verified camera=%s session=%s: %s",
                            camera,
                            session.pk,
                            stop_exc,
                        )
                    raise
            elif session.conveyor_enabled:
                _assert_conveyor_binding(session, live_payload)
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
                order = begin_camera_loading(
                    order,
                    camera,
                    user,
                    reservation_id=session.pk,
                )
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

    # The edge session was prepared with its output proven OFF.  Only after
    # the order/session transaction above is durable may the physical relay be
    # energized.  An ambiguous timeout therefore leaves a visible loading
    # session that an operator can stop/reconcile; it never runs for a merely
    # confirmed order.
    if session.conveyor_enabled:
        try:
            live_payload = _payload(ai.start_conveyor(camera, session.pk))
            _assert_conveyor_confirmed_on(session, live_payload)
        except (ai.AiError, ai.AiUnavailable):
            try:
                stopped_payload = _payload(
                    ai.emergency_stop_conveyor(camera, session.pk)
                )
                _assert_conveyor_physical_off(session, stopped_payload)
            except (ai.AiError, ai.AiUnavailable) as stop_exc:
                log.critical(
                    "CRITICAL: fail-safe OFF after unconfirmed conveyor start "
                    "was not verified "
                    "camera=%s session=%s: %s",
                    camera,
                    session.pk,
                    stop_exc,
                )
            raise
        with transaction.atomic():
            current = _locked_open_session(camera)
            if current is not None and current.pk == session.pk:
                _save_live_status_locked(current, live_payload)
                session = current
    return {
        **_with_control(session, live_payload),
        **metadata(session, order.pk, camera, user),
    }


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


def _assert_conveyor_binding(
    session: AiCountingSession,
    payload: Mapping,
) -> None:
    conveyor = _conveyor_status(session, payload)
    if not conveyor.get("configured"):
        return
    bound_session = conveyor.get("session_id", payload.get("session_id"))
    bound_target = conveyor.get("target_total", payload.get("target_total"))
    if (
        not _same_session_id(bound_session, session.pk)
        or type(bound_target) is not int
        or bound_target != session.target_total
    ):
        raise ai.AiError(
            409,
            "Контроллер потерял привязку заказа; автоматический перезапуск запрещён. "
            "Остановите оборудование и выполните ручную сверку",
        )


def _assert_conveyor_prepared_off(
    session: AiCountingSession,
    payload: Mapping,
) -> None:
    conveyor = _conveyor_status(session, payload)
    if not conveyor.get("configured"):
        return
    _assert_conveyor_binding(session, payload)
    if (
        conveyor.get("desired") != 0
        or conveyor.get("feedback") != 0
        or conveyor.get("online") is not True
        or conveyor.get("terminal") is True
        or conveyor.get("state") not in {"off", "prepared", "armed"}
    ):
        raise ai.AiError(
            503,
            "ESP32 не подтвердил безопасную подготовку конвейера в состоянии OFF",
        )


def _assert_conveyor_goal_reached(
    session: AiCountingSession,
    payload: Mapping,
) -> None:
    _assert_conveyor_binding(session, payload)
    total = _valid_total(payload.get("total"))
    if session.target_total <= 0 or total is None or total < session.target_total:
        raise ai.AiError(
            409,
            "Цель заказа ещё не достигнута; завершение отгрузки заблокировано",
        )


def _assert_conveyor_confirmed_off(
    session: AiCountingSession,
    payload: Mapping,
) -> None:
    conveyor = _conveyor_status(session, payload)
    if not conveyor.get("configured"):
        return
    _assert_conveyor_binding(session, payload)
    state = conveyor.get("state")
    if (
        conveyor.get("desired") != 0
        or conveyor.get("feedback") != 0
        or conveyor.get("online") is not True
        or state not in {"off", "goal_reached"}
    ):
        raise ai.AiError(
            503,
            "ESP32 не подтвердил остановку конвейера. Используйте аварийный стоп",
        )


def _assert_conveyor_physical_off(
    session: AiCountingSession,
    payload: Mapping,
) -> None:
    """Fresh emergency OFF proof; processor/session binding may be gone."""
    conveyor = _conveyor_status(session, payload)
    if not conveyor.get("configured"):
        raise ai.AiError(503, "Контроллер конвейера не найден на ПК камеры")
    if (
        conveyor.get("desired") != 0
        or conveyor.get("feedback") != 0
        or conveyor.get("online") is not True
        or conveyor.get("state") not in {"off", "goal_reached"}
    ):
        raise ai.AiError(
            503,
            "ESP32 не подтвердил физический OFF; используйте аппаратный E-stop",
        )


def _assert_conveyor_confirmed_on(
    session: AiCountingSession,
    payload: Mapping,
) -> None:
    conveyor = _conveyor_status(session, payload)
    _assert_conveyor_binding(session, payload)
    if (
        not conveyor.get("configured")
        or conveyor.get("desired") != 1
        or conveyor.get("feedback") != 1
        or conveyor.get("online") is not True
        or conveyor.get("state") != "running"
    ):
        raise ai.AiError(
            503,
            "ESP32 не подтвердил запуск конвейера; отправлена безопасная остановка",
        )


def stop_conveyor(
    camera: str,
    order: Order,
    user,
    *,
    expected_session_id: int | None = None,
) -> dict:
    """Physically stop the belt without closing the counting/order session."""
    camera = ai.normalize(camera)
    assert_device_camera(user, camera)
    cloud_stop = None
    # Serialize with start's long preparation transaction. If STOP was pressed
    # while `/session` was still preparing, this lock is acquired immediately
    # after that commit and the edge terminal-OFF wins over the subsequent ON.
    with transaction.atomic():
        session = _locked_open_session(camera)
        _assert_expected_session(session, expected_session_id)
        if session is None:
            raise ai.AiError(409, "Активная AI-сессия не найдена")
        if session.order_id != order.pk:
            raise sessions.AiSessionBusy(session)

        # OFF is intentionally less restrictive than start/reset/complete:
        # any scoped shipping operator may de-energize it.
        if session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD:
            device = cloud_conveyors.cloud_device_for(camera)
            if device is None or device.command_session_id != session.pk:
                raise ai.AiError(503, "Cloud conveyor device binding is missing")
            cloud_stop = cloud_conveyors.emergency_stop(
                device, "emergency_stop",
            )
            live_payload = {}
        elif session.conveyor_enabled:
            live = ai.emergency_stop_conveyor(camera, session.pk)
            _assert_conveyor_physical_off(session, live)
        else:
            live = ai.status(camera) or {}
            status_payload = _payload(live)
            if _conveyor_status(session, status_payload).get("configured"):
                live = ai.emergency_stop_conveyor(camera, session.pk)
                _assert_conveyor_physical_off(session, live)

        if cloud_stop is None:
            live_payload = _payload(live)
            _save_live_status_locked(session, live_payload)
    if cloud_stop is not None:
        try:
            confirmed = cloud_conveyors.wait_confirmed(
                cloud_stop.pk,
                cloud_stop.command_revision,
                False,
                float(getattr(settings, "CONVEYOR_COMMAND_TIMEOUT_SECONDS", 5)),
            )
        except cloud_conveyors.ConveyorDeviceError as exc:
            raise _cloud_ai_error(exc) from exc
        live_payload = _payload(ai.status(camera))
        live_payload["conveyor"] = cloud_conveyors.control_payload(confirmed)
        with transaction.atomic():
            current = _locked_open_session(camera)
            if current is not None and current.pk == session.pk:
                _save_live_status_locked(current, live_payload)
                session = current
    log_event(
        "conveyor_stop",
        f"Конвейер {camera} остановлен без завершения заказа #{order.pk}",
        user=user,
        order=order,
        payload={
            "camera": camera,
            "session_id": session.pk,
            "target_total": session.target_total,
            "total": _valid_total(live_payload.get("total")),
            "conveyor": _conveyor_status(session, live_payload),
        },
    )
    return {
        **(
            _with_cloud_control(session, live_payload)
            if session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD
            else _with_control(session, live_payload)
        ),
        **metadata(session, order.pk, camera, user),
    }


def _stop_cloud(
    camera: str,
    order: Order,
    user,
    session: AiCountingSession,
    *,
    complete_order: bool,
    expected_session_id: int | None,
) -> dict:
    """Close a cloud session using heartbeat proof, never direct Modbus."""
    with transaction.atomic():
        locked = _locked_open_session(camera)
        _assert_expected_session(locked, expected_session_id)
        if locked is None or locked.pk != session.pk:
            raise ai.AiError(409, "AI session changed during completion")
        if locked.order_id != order.pk:
            raise sessions.AiSessionBusy(locked)
        if not can_control_session(locked, user):
            raise PermissionDenied(
                "Only the employee who started loading or an administrator "
                "may stop this conveyor"
            )
        device = cloud_conveyors.cloud_device_for(camera)
        if device is None or device.command_session_id != locked.pk:
            raise ai.AiError(503, "Cloud conveyor device binding is missing")
        if complete_order and (
            locked.target_total <= 0 or device.last_total < locked.target_total
        ):
            raise ai.AiError(
                409, "The order target has not yet been reached",
            )
        stopped = cloud_conveyors.emergency_stop(
            device,
            "target_reached" if complete_order else "manual_stop",
        )

    timeout = float(getattr(settings, "CONVEYOR_COMMAND_TIMEOUT_SECONDS", 5))
    try:
        confirmed = cloud_conveyors.wait_confirmed(
            stopped.pk, stopped.command_revision, False, timeout,
        )
    except cloud_conveyors.ConveyorDeviceError as exc:
        raise _cloud_ai_error(exc) from exc

    exit_reading = None
    if complete_order and order.status == "loading":
        exit_reading = read_scale_exit_if_required(order)

    # Require a heartbeat produced after the potentially slow scale operation;
    # cached preflight OFF is not enough for an irreversible shipment.
    proof_after = timezone.now()
    try:
        confirmed = cloud_conveyors.wait_confirmed(
            stopped.pk,
            stopped.command_revision,
            False,
            timeout,
            seen_after=proof_after,
        )
    except cloud_conveyors.ConveyorDeviceError as exc:
        raise _cloud_ai_error(exc) from exc

    capture_failure = None
    try:
        final = _payload(ai.status(camera))
    except (ai.AiError, ai.AiUnavailable) as exc:
        final = {}
        capture_failure = exc
    final["total"] = confirmed.last_total
    final["conveyor"] = cloud_conveyors.control_payload(confirmed)
    actual_bags = None

    with transaction.atomic():
        locked = _locked_open_session(camera)
        if locked is None or locked.pk != session.pk:
            raise ai.AiError(409, "AI session changed during completion")
        _assert_expected_session(locked, expected_session_id)
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        device = cloud_conveyors.cloud_device_for(camera, lock=True)
        if (
            device is None
            or device.command_session_id != locked.pk
            or device.command_revision != stopped.command_revision
            or not cloud_conveyors.confirmed_state(device, False)
        ):
            raise ai.AiError(
                503, "ESP32 did not provide a fresh physical OFF proof",
            )
        if complete_order and (
            locked.status != AiCountingSession.ACTIVE
            or locked_order.status != "loading"
            or device.last_total < locked.target_total
        ):
            raise ai.AiError(409, "Cloud conveyor target/session is not complete")

        if complete_order:
            shipment = finish_ai_loading(
                locked_order,
                device.last_total,
                user,
                exit_reading=exit_reading,
            )
            actual_bags = shipment.bags_loaded
            final_total = actual_bags
        else:
            _release_camera_binding(locked_order.pk, camera)
            final_total = device.last_total

        locked.status = AiCountingSession.CLOSED
        locked.closed_by = user
        locked.ended_at = timezone.now()
        locked.final_total = final_total
        locked.last_status = final
        stream = _stream(final)
        if stream:
            locked.recording_stream = stream
        locked.error = _cleanup_error(
            capture_failure or RuntimeError("cleanup scheduled")
        )
        locked.save(
            update_fields=[
                "status", "closed_by", "ended_at", "final_total",
                "last_status", "recording_stream", "error",
            ]
        )
        session = locked
        if not complete_order:
            log_event(
                "ai_manual_reconcile",
                f"AI-session {locked.pk} closed for manual reconciliation",
                user=user,
                order=order,
                payload={
                    "camera": camera,
                    "session_id": locked.pk,
                    "reason": "operator_manual_reconciliation",
                    "target_total": locked.target_total,
                    "captured_total": device.last_total,
                },
            )

    cleanup_failure = capture_failure
    if cleanup_failure is None:
        try:
            ai.delete(camera)
        except (ai.AiError, ai.AiUnavailable) as exc:
            cleanup_failure = exc
    if cleanup_failure is None:
        AiCountingSession.objects.filter(pk=session.pk).update(error="")
    else:
        AiCountingSession.objects.filter(pk=session.pk).update(
            error=_cleanup_error(cleanup_failure)
        )

    response = {
        **final,
        "running": False,
        "available": True,
        "busy": False,
        "owned_by_order": False,
    }
    if cleanup_failure is not None:
        response["cleanup_pending"] = True
    if complete_order:
        response.update(order_status="shipped", bags_loaded=actual_bags)
    return _with_cloud_control(session, response)


def stop(
    camera: str,
    order: Order,
    user,
    *,
    complete_order: bool = False,
    expected_session_id: int | None = None,
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

    # A physical output is stopped and read back before any potentially slow
    # exit-scale request or irreversible shipment transition.  Failure keeps
    # ownership open and visible; it is never downgraded to a cleanup warning.
    preflight_session = sessions.current_for_camera(camera)
    _assert_expected_session(preflight_session, expected_session_id)
    preflight_session_id = (
        preflight_session.pk if preflight_session is not None else None
    )
    if preflight_session is not None:
        if preflight_session.order_id != order.pk:
            raise sessions.AiSessionBusy(preflight_session)
        if not can_control_session(preflight_session, user):
            raise PermissionDenied(
                "Завершить отгрузку может только начавший её сотрудник "
                "или администратор"
            )
        if (
            preflight_session.conveyor_transport
            == AiCountingSession.CONVEYOR_CLOUD
        ):
            return _stop_cloud(
                camera,
                order,
                user,
                preflight_session,
                complete_order=complete_order,
                expected_session_id=expected_session_id,
            )
        if preflight_session.conveyor_enabled and complete_order:
            # Normal completion must prove the target before stopping. Manual
            # cancellation is intentionally deferred until the OPEN row is
            # locked below: that lock serializes with STARTING -> ACTIVE and
            # guarantees a delayed post-commit ON loses to emergency OFF.
            goal_snapshot = _payload(ai.status(camera))
            _assert_conveyor_goal_reached(
                preflight_session,
                goal_snapshot,
            )
            stopped = ai.stop_conveyor(camera, preflight_session.pk)
            _assert_conveyor_confirmed_off(preflight_session, stopped)

    # Внешний сервис может отвечать до нескольких секунд. Ни строка заказа,
    # ни AI-сессия в это время не заблокированы; транзакция ниже повторно
    # валидирует их перед единой записью веса, склада и статуса.
    if (
        complete_order
        and order.status == "loading"
        and preflight_session is not None
        and preflight_session.status == AiCountingSession.ACTIVE
    ):
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
        if preflight_session_id is None or session.pk != preflight_session_id:
            raise ai.AiError(
                409,
                "AI-сессия изменилась во время завершения; обновите страницу",
            )
        _assert_expected_session(session, expected_session_id)
        if session.order_id != order.pk:
            raise sessions.AiSessionBusy(session)
        if not can_control_session(session, user):
            raise PermissionDenied(
                "Остановить отгрузку может только начавший её сотрудник или администратор"
            )
        session_id = session.pk

        if session.conveyor_enabled and not complete_order:
            stopped = ai.emergency_stop_conveyor(camera, session.pk)
            _assert_conveyor_physical_off(session, stopped)

        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if complete_order and (
            session.status != AiCountingSession.ACTIVE
            or locked_order.status != "loading"
        ):
            raise ai.AiError(
                409,
                "Сначала восстановите запуск AI-счётчика или отмените его",
            )

        if complete_order and session.conveyor_enabled:
            # The first OFF/readback happened before the potentially slow exit
            # scale. Repeat the idempotent command under the session row lock
            # and use that single fresh response as both the final count and
            # the last physical proof immediately before the irreversible
            # shipment transition. A controller that went online/ON/fault
            # while the scale was read therefore blocks completion.
            final = _payload(ai.stop_conveyor(camera, session.pk))
            _assert_conveyor_goal_reached(session, final)
            _assert_conveyor_confirmed_off(session, final)
            safe_total = _save_final_snapshot(session, final)
            cleanup_needed = True
            capture_failure = None
        else:
            # Capture while ownership is locked, but do not stop the worker
            # until the order and final snapshot have committed. A business
            # validation error can then roll back safely without destroying
            # the only live copy of the count.
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

        if not complete_order and session.conveyor_enabled:
            log_event(
                "ai_manual_reconcile",
                f"AI-сессия {session.pk} закрыта для ручной сверки без отгрузки",
                user=user,
                order=order,
                payload={
                    "camera": camera,
                    "session_id": session.pk,
                    "reason": "operator_manual_reconciliation",
                    "target_total": session.target_total,
                    "captured_total": _valid_total(final.get("total")),
                },
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
    return _with_control(session, response)


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
        if not can_control_session(session, user):
            raise PermissionDenied(
                "Сбросить счётчик может только начавший отгрузку сотрудник или администратор"
            )
        if session.conveyor_transport == AiCountingSession.CONVEYOR_CLOUD:
            raise ai.AiError(
                409,
                "Cloud-controlled counting cannot be reset during a loading; "
                "stop it and perform a manual reconciliation",
            )
        live = ai.reset(camera)
        live_payload = _payload(live)
        _save_live_status_locked(session, live_payload)
    return {
        **_with_control(session, live_payload),
        **metadata(session, order.pk, camera, user),
    }
