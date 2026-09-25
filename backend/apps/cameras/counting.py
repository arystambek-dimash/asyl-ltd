"""Order-bound workflow for the camera AI counter.

The Windows camera service owns the live counter, while PostgreSQL owns the
business state: which order reserved a camera and whether the loading was
completed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.orders.models import Order
from apps.orders.statuses import CAMERA_BINDING_STATUSES
from apps.shipments.access import assert_can_ship
from apps.shipments.services import (
    begin_camera_loading,
    can_start_loading,
    finish_ai_counting,
)

from . import ai, sessions
from .models import (
    ANALYTICS_SCOPE_SHIPPING,
    AiCountingSession,
    MonoblockCameraSettings,
)
from .policies import (
    can_control_session,
    session_started_by_name,
)

log = logging.getLogger(__name__)

# ``PositiveIntegerField`` uses a signed 32-bit integer on the supported
# databases.  Validate untrusted worker data before it reaches either the AI
# session or Shipment model.
MAX_COUNTER_TOTAL = 2_147_483_647
CLEANUP_PENDING_PREFIX = "AI worker cleanup pending: "


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
        "automatically_started": session.automatically_started,
        "can_stop": can_control_session(session, user),
    }


def _assert_order_department_scope(order_id: int, user) -> Order:
    """Lock Order then Client and recheck ownership before edge effects.

    Счётчиком заказа управляет только грузчик его области («Фуры | Вагоны»);
    системная автоматика (``user=None``) не проверяется.
    """
    from apps.orders.services import lock_live_order

    order = lock_live_order(order_id, user)
    assert_can_ship(user, order)
    return order


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


def _abort_reservation_locked(
    session: AiCountingSession,
    camera: str,
    detail: str,
    *,
    delete_worker: bool,
) -> None:
    """Fail a reservation that never became a loading and free the camera.

    A worker that may already run for this session is idled first; if that
    cleanup fails, the row keeps the cleanup marker for the next start.
    """
    error = detail
    if delete_worker:
        try:
            _delete_exact_session(camera, session.pk)
        except (ai.AiError, ai.AiUnavailable) as cleanup_exc:
            error = _cleanup_error(cleanup_exc)
    session.status = AiCountingSession.FAILED
    session.ended_at = timezone.now()
    session.error = error[:500]
    session.save(update_fields=["status", "ended_at", "error"])
    _release_camera_binding(session.order_id, camera)


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


def _release_camera_binding(order_id: int, camera: str) -> None:
    """Release only the matching active binding, never historical orders."""
    Order.objects.filter(
        pk=order_id,
        loading_camera=camera,
        status__in=CAMERA_BINDING_STATUSES,
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
    return _validated_finished_session(stopped, camera, session_id, invalid_detail)


def _validated_finished_session(stopped, camera, session_id, invalid_detail):
    """Validate the exact frozen identity for manual and guarded completion."""
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


def _validate_start(order: Order, camera: str) -> None:
    # Ownership conflicts are more useful than a generic status error.
    camera_session = sessions.current_for_camera(camera)
    if camera_session and camera_session.order_id != order.pk:
        raise sessions.AiSessionBusy(camera_session)
    order_session = sessions.current_for_order(order.pk)
    if order_session and order_session.camera != camera:
        raise sessions.AiSessionBusy(order_session)

    if not can_start_loading(order, camera):
        raise ai.AiError(
            400,
            "Загрузку можно начать только для подтверждённого или прибывшего заказа",
        )
    if camera not in MonoblockCameraSettings.shipping_sources():
        raise ai.AiError(
            400,
            "Эта камера не разрешена администратором для Моноблока",
        )


def _start_worker(camera: str, session: AiCountingSession) -> dict:
    return ai.start(
        camera,
        {
            "source": "sub",
            "session_id": session.pk,
            "require_continuous": True,
            "expected_analytics_scope": ANALYTICS_SCOPE_SHIPPING,
        },
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

    _validate_start(order, camera)

    with transaction.atomic():
        sessions.lock_camera_binding()
        if (
            user is None
            or not type(user)._default_manager.filter(pk=user.pk, is_active=True).exists()
        ):
            raise PermissionDenied("Учётная запись отключена администратором")
        order = _assert_order_department_scope(order.pk, user)
        existing = sessions.current_for_camera(camera)
        _assert_expected_session(existing, expected_session_id)
        _validate_start(order, camera)

        session, created = sessions.reserve(order, camera, user)
        _assert_expected_session(session, expected_session_id)

    # Raised only after the failed reservation is committed.
    deferred_error: Exception | None = None

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
                live = _start_worker(camera, session)
                worker_may_be_running = True
            else:
                live = ai.status(camera)
                if not ai.is_running_order_session(_payload(live)):
                    live = _start_worker(camera, session)
                worker_may_be_running = live is not None
            live_payload = _payload(
                ai.wait_for_order_session(
                    camera,
                    live,
                    expected_session_id=session.pk,
                )
            )
        except ai.AiError as exc:
            if exc.status < 500 and was_starting:
                _abort_reservation_locked(
                    session,
                    camera,
                    exc.detail,
                    delete_worker=worker_may_be_running,
                )
                deferred_error = exc
            else:
                raise
        else:
            try:
                order = begin_camera_loading(order, camera, user)
            except (ValidationError, PermissionDenied) as exc:
                _abort_reservation_locked(
                    session,
                    camera,
                    str(exc.detail),
                    delete_worker=True,
                )
                deferred_error = exc
            else:
                _activate_locked(session, live_payload)

    if deferred_error is not None:
        raise deferred_error
    return {**live_payload, **metadata(session, order.pk, camera, user)}


def _stored_final(session: AiCountingSession) -> tuple[dict, int | None]:
    snapshot = _payload(session.last_status)
    # ``last_status`` is a UI checkpoint (often the zero returned by start),
    # not proof of a final count. Only ``final_total`` was captured by an
    # explicit stop and is safe when the worker is gone.
    if session.final_total is None:
        snapshot.pop("total", None)
    else:
        snapshot["total"] = session.final_total
    return snapshot, session.final_total


def _capture_final(
    camera: str, session: AiCountingSession
) -> tuple[dict, int | None, Exception | None]:
    """Capture only this loading's total without stopping the worker yet.

    The caller closes the locked row in the same transaction, and the worker
    is always idled afterwards by the scoped cleanup.
    """
    try:
        live = ai.status(camera)
    except (ai.AiError, ai.AiUnavailable) as exc:
        # The request is ambiguous: a session worker can still be running, so
        # the closed row must retain a cleanup marker.
        return (*_stored_final(session), exc)

    if live is None:
        failure = ai.AiError(
            503,
            "AI-процессор не найден; durable session требует очистки",
        )
        return (*_stored_final(session), failure)
    live_payload = _payload(live)
    if live_payload.get("mode") == "session":
        ai.assert_order_session_identity(live_payload, session.pk)
    if not (
        ai.is_running_order_session(live_payload)
        and ai.is_continuous_shipping(live_payload)
    ):
        # After a camera-PC restart the configured 24/7 worker may be back on
        # this camera. Its total belongs to analytics, not to this order. The
        # local row can close, but its durable boundary remains pending until a
        # scoped DELETE proves this exact session was finalized or absent.
        return (*_stored_final(session), None)

    # Never erase a valid snapshot with a malformed response from a later
    # attempt. It may be the only final count left after the worker is idled.
    safe_total = _valid_total(live_payload.get("total"))
    if safe_total is None:
        safe_total = session.final_total
    return live_payload, safe_total, None


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
    if not ai.is_continuous_shipping(final):
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

    Сессию закрывает отгрузка грузчика: право на неё (loader.confirm и
    область транспорта) проверено, а не только у того, кто подсчёт запустил.

    Business completion requires the scoped durable DELETE result before its
    database commit. A plain cancel may still close locally when remote cleanup
    is temporarily broken; :func:`start` retries that marked cleanup before a
    later order can reuse the camera.
    """
    camera = ai.normalize(camera)

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
            shipment = finish_ai_counting(
                locked_order,
                safe_total,
                user,
            )
            actual_bags = shipment.bags_loaded
            final_total = actual_bags
        else:
            final, safe_total, capture_failure = _capture_final(camera, session)
            cleanup_needed = True
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
        **metadata(None, order.pk, camera),
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


def close_session_for_dispatch(order: Order, user) -> None:
    """Грузчик отгружает заказ, по которому открыт AI-подсчёт.

    Активный подсчёт идущей погрузки завершается штатно — в заказ идут мешки,
    посчитанные камерой; незапущенный или сбойный отменяется, и отгрузка
    возьмёт заказанное количество. Ошибки ПК камер пробрасываются: заказ
    остаётся как был, грузчик повторит.
    """
    session = sessions.current_for_order(order.pk)
    if session is None:
        return
    status = Order.objects.filter(pk=order.pk).values_list("status", flat=True).first()
    stop(
        session.camera,
        order,
        user,
        complete_order=session.status == AiCountingSession.ACTIVE and status == "loading",
        expected_session_id=session.pk,
    )

