"""Acquire and complete orders using fresh observations without stopping conveyors.

The worker owns recognition and reservations. HTTP status reads never start OCR
or mutate loading. Completion needs physical absence and a quiet conveyor;
loss of a number alone never proves departure or ends a session.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import close_old_connections, connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.orders.models import Order

from . import ai, counting, sessions, shipping_completion, shipping_tracking, transport_recognition
from .models import (
    AiCountingSession,
    MonoblockCameraSettings,
    ShippingTransportCamera,
    ShippingTransportRecognitionEvent,
    ShippingTransportState,
)

log = logging.getLogger(__name__)
MAX_AGE = timedelta(seconds=15)
MIN_CONFIRMATION_SPAN = timedelta(seconds=2)
CONFIRMATIONS = 3
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def binding_mutex(binding_id: int):
    """Do not overlap expensive recognition or claims on the same conveyor."""
    with _locks_guard:
        local = _locks.setdefault(binding_id, threading.Lock())
    local_acquired = local.acquire(blocking=False)
    acquired = local_acquired
    database_acquired = False
    try:
        if acquired and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)", [0x534854, binding_id]
                )
                database_acquired = cursor.fetchone()[0]
            acquired = database_acquired
        yield acquired
    finally:
        if database_acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s, %s)", [0x534854, binding_id]
                    )
            except Exception:
                connection.close()
        if local_acquired:
            local.release()


def normalize_number(value: str) -> str:
    # Preserve leading zeros; only remove formatting accepted by number inputs.
    return re.sub(r"[\s-]+", "", value or "").upper()


def transport_type(binding: ShippingTransportCamera) -> str:
    return "train" if binding.recognition_model == "wagon_number" else "truck"


def eligible_orders(binding: ShippingTransportCamera):
    return (
        Order.objects.filter(
            status__in=("confirmed", "arrived"),
            transport_type=transport_type(binding),
        )
        .select_related("client__user")
        .order_by("id")
    )


def matching_orders(binding: ShippingTransportCamera, number: str) -> list[Order]:
    return [
        order
        for order in eligible_orders(binding)
        if normalize_number(order.truck_number) == number
    ]


def _ready_tracking(tracking):
    value = shipping_tracking.current_tracking(tracking)
    return (
        value["presence"] == "present"
        and value["motion"] == "stationary"
        and value["number_associated"] is True
        and bool(value["visit_id"])
    )


def observation_is_current(state: ShippingTransportState) -> bool:
    now = timezone.now()
    return bool(
        state.number
        and state.confirmations >= CONFIRMATIONS
        and state.observed_at
        and now - MAX_AGE <= state.observed_at <= now + timedelta(seconds=5)
        and state.candidate_since
        and state.observed_at - state.candidate_since >= MIN_CONFIRMATION_SPAN
        and _ready_tracking(state.tracking)
        and state.candidate_visit_id == state.tracking["visit_id"]
    )


def _reset_votes(state):
    state.number = ""
    state.candidate_number = ""
    state.candidate_visit_id = ""
    state.confirmations = 0
    state.candidate_since = None


def _set_state(state, phase: str, detail: str = ""):
    state.state = phase
    state.detail = detail[:500]
    state.polled_at = timezone.now()
    state.save()


def _binding_is_current(binding):
    return (
        ShippingTransportCamera.objects.filter(
            pk=binding.pk,
            updated_at=binding.updated_at,
        ).exists()
        and binding.conveyor_camera in MonoblockCameraSettings.shipping_sources()
    )


def _state_for(binding):
    state, _ = ShippingTransportState.objects.get_or_create(
        conveyor_camera=binding.conveyor_camera,
        defaults={"binding": binding, "configuration_updated_at": binding.updated_at},
    )
    if (
        state.binding_id != binding.pk
        or state.configuration_updated_at != binding.updated_at
    ):
        _reset_votes(state)
        state.configuration_updated_at = binding.updated_at
        state.binding = binding
        state.frame_ids = []
        state.observed_at = None
        state.tracking = shipping_tracking.unknown_tracking(
            "configuration_changed", observed_at=timezone.now()
        )
        state.auto_finish = shipping_completion.status()
        # Camera edits do not prove departure and cannot release a closed visit.
        state.evidence = None
        _set_state(state, "waiting_number")
    return state


def _same_completed_visit(state, number=None) -> bool:
    """Keep a completed loading latched until a proven clear or explicit exit.

    Neither OCR text nor a newly assigned CV track identity proves departure.
    The optional number argument is retained for existing call sites/tests.
    """
    if not state.session_id or state.session.status != AiCountingSession.CLOSED:
        return False
    session = state.session
    if session.order.status != "loaded":
        return False
    if (
        session.automatically_started
        and isinstance(session.last_status, dict)
        and session.last_status.get("auto_finish", {}).get("state") == "completed"
        and session.last_status.get("automatic_finish")
    ):
        # Automatic completion itself requires confirmed departure. A new
        # arrival between finish and the next poll must not need a second
        # post-finish empty frame or an operator's shipment action.
        return False
    return not (
        session.ended_at
        and state.departure_observed_at
        and state.departure_observed_at > session.ended_at
    )


def _record_evidence(
    binding,
    state,
    observation,
    status,
    *,
    number=None,
    owner=None,
    visit_id=None,
    last_seen_at=None,
):
    """Keep separate visits separate, including repeat plates and active anomalies."""
    number = observation.number if number is None else number
    number = number or ""
    visit_id = (state.tracking.get("visit_id") or "") if visit_id is None else visit_id
    evidence = state.evidence
    alert = status == "tracking_alert"
    observed = status == "observed"
    presence = state.tracking.get("presence")
    absent_since = state.tracking.get("absent_since")
    key = (number, visit_id, binding.number_camera, binding.recognition_model)
    if evidence and (
        (
            evidence.number,
            evidence.visit_id,
            evidence.number_camera,
            evidence.recognition_model,
        )
        != key
        or (evidence.order_id and evidence.order.status == "shipped")
        or (evidence.status == "tracking_alert") != alert
        or (evidence.status == "observed") != observed
        or (
            (alert or observed) and evidence.session_id != (owner.pk if owner else None)
        )
        or (
            observed
            and not visit_id
            and observation.observed_at - evidence.last_seen_at > MAX_AGE
        )
        or (alert and evidence.tracking.get("presence") != presence)
        or (
            alert
            and presence == "absent"
            and evidence.tracking.get("absent_since") != absent_since
        )
    ):
        evidence = None
    if evidence is None and (visit_id or observed):
        candidates = ShippingTransportRecognitionEvent.objects.filter(
            conveyor_camera=binding.conveyor_camera,
            number=number,
            visit_id=visit_id,
            number_camera=binding.number_camera,
            recognition_model=binding.recognition_model,
        ).exclude(order__status="shipped")
        if alert:
            candidates = candidates.filter(
                status="tracking_alert", tracking__presence=presence, session=owner
            )
            if presence == "absent":
                candidates = candidates.filter(tracking__absent_since=absent_since)
        elif observed:
            candidates = candidates.filter(status="observed", session=owner)
            if not visit_id:
                candidates = candidates.filter(
                    last_seen_at__gte=observation.observed_at - MAX_AGE
                )
        else:
            candidates = candidates.exclude(status__in=("tracking_alert", "observed"))
        evidence = candidates.order_by("-id").first()
    if owner is None and not (observed or alert) and _same_completed_visit(state):
        if number == state.claimed_number and visit_id == state.claimed_visit_id:
            owner = state.session
            status = "matched"
    if evidence is None:
        evidence = ShippingTransportRecognitionEvent(
            conveyor_camera=binding.conveyor_camera,
            number_camera=binding.number_camera,
            recognition_model=binding.recognition_model,
            number=number,
            visit_id=visit_id,
            first_seen_at=last_seen_at
            or state.candidate_since
            or observation.observed_at,
            last_seen_at=last_seen_at or observation.observed_at,
            status=status,
        )
        if observation.snapshot:
            evidence.image.save(
                f"{uuid.uuid4().hex}.jpg", ContentFile(observation.snapshot), save=False
            )
    evidence.last_seen_at = last_seen_at or observation.observed_at
    evidence.tracking = state.tracking
    if owner is not None:
        evidence.order_id = owner.order_id
        evidence.session_id = owner.pk
        evidence.status = status
    elif evidence.session_id is None:
        evidence.status = status
    evidence.save()
    state.evidence = evidence


def _vote(state, observation, *, gap):
    visit_id = state.tracking.get("visit_id") or ""
    if not observation.number or not _ready_tracking(state.tracking):
        _reset_votes(state)
        return False
    if (
        gap
        or state.candidate_number != observation.number
        or state.candidate_visit_id != visit_id
    ):
        _reset_votes(state)
        state.candidate_number = observation.number
        state.candidate_visit_id = visit_id
        state.candidate_since = observation.observed_at
    state.confirmations = min(CONFIRMATIONS, state.confirmations + 1)
    if (
        state.confirmations < CONFIRMATIONS
        or observation.observed_at - state.candidate_since < MIN_CONFIRMATION_SPAN
    ):
        return False
    state.number = observation.number
    return True


def _observe(binding, state):
    """Observe every lane even while its order counter is active or unavailable."""
    observation = None
    failure = None
    try:
        observation = transport_recognition.observe_transport(
            binding.number_camera, binding.recognition_model,
            **({"zone": binding.loading_zone} if binding.loading_zone is not None else {}),
        )
    except (ai.AiError, ai.AiUnavailable) as exc:
        failure = exc
    with transaction.atomic():
        sessions.lock_camera_binding()
        if not _binding_is_current(binding):
            return None, False, False
        state.refresh_from_db()
        current = sessions.current_for_camera(binding.conveyor_camera)
        if observation is not None and (
            observation.frame_id in state.frame_ids
            or (state.observed_at and observation.observed_at <= state.observed_at)
            or not timezone.now() - MAX_AGE
            <= observation.observed_at
            <= timezone.now() + timedelta(seconds=5)
        ):
            observation = None
            failure = ai.AiUnavailable("Камера не передаёт новые кадры")
        if failure is not None:
            _reset_votes(state)
            state.tracking = shipping_tracking.unknown_tracking(
                "observation_unavailable", observed_at=timezone.now()
            )
            if state.auto_finish.get("state") != "finishing":
                state.auto_finish = shipping_completion.status(
                    "blocked", "Нет свежего наблюдения транспорта; погрузка сохранена"
                )
            _set_state(
                state,
                "error",
                "Нет свежего наблюдения транспорта; привязка погрузки сохранена",
            )
            return None, True, True
        gap = (
            state.observed_at is None
            or observation.observed_at - state.observed_at > MAX_AGE
        )
        state.observed_at = observation.observed_at
        state.frame_ids = [*state.frame_ids[-15:], observation.frame_id]
        state.tracking = shipping_tracking.current_tracking(observation.tracking)
        if state.tracking["presence"] == "unknown":
            state.tracking["observed_at"] = observation.observed_at.isoformat()
        confirmed = _vote(state, observation, gap=gap)
        tracking = state.tracking
        recorded_alert = False
        if tracking["presence"] == "absent":
            # An absence seen before closure cannot release a future closed
            # visit. The next fresh absent frame after closure can do so.
            state.departure_observed_at = observation.observed_at
            if current:
                state.tracking_alert = "Транспорт не обнаружен, но сессия погрузки ещё открыта. Проверьте погрузку"
            owner = current or (
                state.session
                if state.session_id
                and state.session.status == AiCountingSession.CLOSED
                and state.session.order.status != "shipped"
                else None
            )
            if owner:
                previous_evidence = (
                    ShippingTransportRecognitionEvent.objects.filter(
                        session=owner,
                        status="matched",
                    )
                    .order_by("-last_seen_at")
                    .first()
                )
                # The empty frame is an observation of absence, not a newer
                # sighting of the number/body. Keep the last actual sighting.
                raw_last_seen = tracking.get("last_seen_at")
                last_seen = parse_datetime(raw_last_seen) if raw_last_seen else None
                last_seen = last_seen or (
                    previous_evidence.last_seen_at
                    if previous_evidence
                    else owner.activated_at or owner.started_at
                )
                _record_evidence(
                    binding,
                    state,
                    observation,
                    "tracking_alert",
                    owner=owner,
                    number=state.claimed_number
                    or normalize_number(owner.order.truck_number),
                    visit_id=state.claimed_visit_id or tracking.get("visit_id") or "",
                    last_seen_at=last_seen,
                )
                recorded_alert = True
        elif tracking["presence"] == "present" and current:
            expected_number = (
                state.claimed_number
                if state.session_id == current.pk
                else normalize_number(current.order.truck_number)
            )
            changed_visit = bool(
                state.claimed_visit_id
                and tracking.get("visit_id") != state.claimed_visit_id
            )
            changed_number = bool(
                confirmed and expected_number and observation.number != expected_number
            )
            if changed_visit or changed_number:
                state.tracking_alert = (
                    "У конвейера другой транспорт; текущая сессия сохранена. Проверьте погрузку"
                    if changed_number
                    else "Изменилось наблюдение транспорта; проверьте погрузку. Текущая сессия сохранена"
                )
                _record_evidence(
                    binding, state, observation, "tracking_alert", owner=current
                )
                recorded_alert = True
            elif (
                state.session_id == current.pk
                and tracking.get("visit_id") == state.claimed_visit_id
            ):
                # A hidden plate does not lose the last known transport or its
                # photo: body presence extends that same physical visit.
                evidence = (
                    ShippingTransportRecognitionEvent.objects.filter(
                        session=current,
                        number=state.claimed_number,
                        visit_id=state.claimed_visit_id,
                        status="matched",
                    )
                    .order_by("-id")
                    .first()
                )
                if evidence:
                    evidence.last_seen_at = observation.observed_at
                    evidence.tracking = tracking
                    evidence.save(update_fields=["last_seen_at", "tracking"])
            if not state.claimed_visit_id and confirmed:
                _record_evidence(binding, state, observation, "matched", owner=current)
        if observation.number and not _ready_tracking(tracking) and not recorded_alert:
            # A readable number can still be useful evidence when the body
            # detector is unavailable or cannot associate it with this lane.
            # It never contributes votes or selects an order.
            _record_evidence(binding, state, observation, "observed", owner=current)
        if confirmed and not current:
            _record_evidence(binding, state, observation, "no_order")
        _set_state(
            state,
            "confirming" if confirmed or state.confirmations else "waiting_number",
        )
    return observation, False, True


def acquire(binding, state, order):
    """Fence fresh stationary presence, visit, order edits and the reservation."""
    number = state.number
    visit_id = state.candidate_visit_id
    configuration = binding.updated_at

    def check(locked_order):
        if not _binding_is_current(binding) or binding.updated_at != configuration:
            raise ValidationError("Настройки камеры изменились. Обновите страницу")
        current = ShippingTransportState.objects.select_for_update().get(pk=state.pk)
        if (
            current.number != number
            or current.candidate_visit_id != visit_id
            or not observation_is_current(current)
        ):
            raise ValidationError(
                "Стоящий транспорт и номер больше не подтверждены свежими кадрами"
            )
        if _same_completed_visit(current):
            raise ValidationError("Предыдущий транспорт ещё не подтвердил выезд")
        if locked_order.transport_type != transport_type(binding):
            raise ValidationError("Тип транспорта заказа не совпадает с камерой номера")
        matches = matching_orders(binding, number)
        if len(matches) != 1 or matches[0].pk != locked_order.pk:
            raise ValidationError("Подходящий заказ изменился; повторяем сопоставление")

    def reserved(session):
        continuing = state.session_id == session.pk
        state.session = session
        state.claimed_number = number
        state.claimed_visit_id = visit_id
        if not continuing:
            state.departure_observed_at = None
            state.tracking_alert = ""
            state.auto_finish = shipping_completion.status()
        if state.evidence_id:
            ShippingTransportRecognitionEvent.objects.filter(
                pk=state.evidence_id
            ).update(
                order_id=session.order_id,
                session=session,
                status="matched",
            )
        _set_state(state, "starting", "Привязываем заказ к непрерывному счётчику")

    def check_remote_start(session):
        current = ShippingTransportState.objects.select_for_update().get(pk=state.pk)
        if (
            not _binding_is_current(binding)
            or current.session_id != session.pk
            or current.number != number
            or current.candidate_visit_id != visit_id
            or current.claimed_number != number
            or current.claimed_visit_id != visit_id
            or not observation_is_current(current)
        ):
            # The first reservation transaction has committed. A transient
            # failure preserves that exact session for fresh observation retry.
            raise ai.AiUnavailable(
                "Перед запуском нужны свежие кадры стоящего транспорта и номера"
            )

    result = counting.start(
        binding.conveyor_camera,
        order,
        None,
        automatic=True,
        before_reserve=check,
        after_reserve=reserved,
        before_remote_start=check_remote_start,
    )
    _set_state(state, "loading", "Заказ автоматически привязан к конвейеру")
    return result


def _reconcile_counter(binding, state, current):
    """An acknowledged durable start remains valid when a plate is occluded."""
    if not current:
        return "none"
    if not current.automatically_started or state.session_id != current.pk:
        return "busy"
    live = ai.status(binding.conveyor_camera)
    state._live_counter = live
    if live and live.get("mode") == "session":
        ai.assert_order_session_identity(live, current.pk)
    ready = ai._order_session_ready(live, expected_session_id=current.pk)
    if current.status == AiCountingSession.ACTIVE or ready:
        if not ready or current.status == AiCountingSession.STARTING:
            state._live_counter = counting.start(
                binding.conveyor_camera,
                current.order,
                None,
                automatic=True,
                expected_session_id=current.pk,
            )
        return "loading"
    return "starting"


def _attempt_completion(binding, state, *, recovery_only=False):
    """A durable intent must resolve its receipt before any counter restoration."""
    intent = state.auto_finish
    session_id = intent.get("session_id")
    session = AiCountingSession.objects.select_related("order").filter(pk=session_id).first()
    if session is None or state.session_id != session.pk:
        state.auto_finish = shipping_completion.status("blocked", "Привязка погрузки изменилась")
        _set_state(state, "error", "Привязка погрузки изменилась")
        return True
    if session.status in (AiCountingSession.CLOSED, AiCountingSession.FAILED) and not (
        session.status == AiCountingSession.CLOSED
        and session.order.status in ("loaded", "shipped")
        and session.final_total is not None
    ):
        # An explicit cancellation/terminal failure supersedes our old intent.
        # Normal start still honors the session's durable cleanup marker.
        state.auto_finish = shipping_completion.status("blocked", "Автоматическое завершение отменено: сессия закрыта")
        _set_state(state, "waiting_number", "Предыдущая сессия закрыта; ожидаем следующую погрузку")
        return False

    def check(locked_session):
        owned = ShippingTransportState.objects.select_for_update().get(pk=state.pk)
        if not _binding_is_current(binding) or owned.session_id != locked_session.pk:
            raise ValidationError("Настройки или привязка погрузки изменились")
        if not recovery_only:
            body = shipping_tracking.current_tracking(owned.tracking)
            if (
                body["presence"] != "absent"
                or body["absent_since"] != intent.get("transport_absent_since")
                or body["visit_id"] != intent.get("transport_visit_id")
            ):
                raise ValidationError("Перед завершением нужно свежее подтверждение отсутствия транспорта")

    try:
        result = counting.complete_automatic(
            binding.conveyor_camera, session.order,
            expected_session_id=session.pk,
            guard=shipping_completion.guard(intent, recovery_only=recovery_only),
            before_remote_finish=check,
        )
    except ai.AiError as exc:
        # Only these typed rejections prove the remote session was not frozen.
        # A timeout, malformed reply or any other error preserves the intent.
        if exc.status == 409 and exc.payload.get("code") in (
            "auto_finish_guard_rejected", "auto_finish_not_completed",
        ):
            state.auto_finish = shipping_completion.status(
                "blocked", "Условия завершения изменились; повторно проверяем транспорт и конвейер"
            )
            _set_state(state, "loading", "Погрузка активна; проверка завершения начнётся заново")
            return False
        _set_state(state, "error", "Уточняем итог погрузки на ПК камер; привязка сохранена")
        return True
    except (ai.AiUnavailable, ValidationError, PermissionDenied, sessions.AiSessionBusy):
        _set_state(state, "error", "Уточняем итог погрузки на ПК камер; привязка сохранена")
        return True
    state.refresh_from_db()
    state.auto_finish = shipping_completion.status(
        "completed", "Погрузка завершена автоматически: транспорт отсутствовал и конвейер был свободен 40 секунд",
        remaining_seconds=0, session_id=session.pk, total=result.get("total"),
    )
    state.tracking_alert = ""
    _set_state(state, "completed", "Итоговый счёт сохранён в заказе. Можно оформить выезд")
    return False


def _consider_completion(binding, state, current):
    if not current or not current.automatically_started or state.session_id != current.pk:
        return False
    # This write commits before the remote finish. After a lost response the
    # next poll queries only the durable receipt, never restarts the counter.
    with transaction.atomic():
        sessions.lock_camera_binding()
        if not _binding_is_current(binding):
            return False
        live = getattr(state, "_live_counter", None)
        state.refresh_from_db()
        current.refresh_from_db()
        if current.status != AiCountingSession.ACTIVE or state.session_id != current.pk:
            return False
        state.auto_finish = shipping_completion.evaluate(
            state.auto_finish, state.tracking, live, current.pk,
        )
        ready = shipping_completion.ready(state.auto_finish)
        if ready:
            state.auto_finish.update(state="finishing", detail="Сохраняем итоговый счёт в заказе")
        _set_state(state, "loading", "Идёт погрузка распознанного транспорта")
    if ready:
        return _attempt_completion(binding, state)
    return False


def _poll_binding(binding_id: int) -> bool:
    with binding_mutex(binding_id) as acquired:
        if not acquired:
            return False
        with transaction.atomic():
            sessions.lock_camera_binding()
            binding = ShippingTransportCamera.objects.filter(pk=binding_id).first()
            if (
                binding is None
                or binding.conveyor_camera
                not in MonoblockCameraSettings.shipping_sources()
            ):
                return False
            state = _state_for(binding)
            # Explicit shipment starts a new eligibility cycle. Mere text or
            # tracker-ID changes never release the previous completed visit.
            if state.session_id and state.session.status == AiCountingSession.CLOSED:
                previous_order = state.session.order
                if previous_order.status == "shipped":
                    shipped_at = previous_order.shipment.shipped_at
                    if (
                        shipped_at
                        and state.candidate_since
                        and state.candidate_since <= shipped_at
                    ):
                        _reset_votes(state)
                        state.save()
        observation, observation_error, configured = _observe(binding, state)
        if not configured:
            return False
        if state.auto_finish.get("state") == "finishing":
            return _attempt_completion(binding, state, recovery_only=True)
        current = sessions.current_for_camera(binding.conveyor_camera)
        try:
            counter_state = _reconcile_counter(binding, state, current)
        except (
            ai.AiError,
            ai.AiUnavailable,
            ValidationError,
            PermissionDenied,
            sessions.AiSessionBusy,
        ):
            state.auto_finish = shipping_completion.status(
                "blocked", "Счётчик недоступен; автоматическое завершение приостановлено"
            )
            _set_state(
                state, "error", "Счётчик погрузки недоступен; привязка сохранена"
            )
            return True
        if counter_state == "loading":
            return _consider_completion(binding, state, current) or observation_error
        if counter_state == "busy":
            _set_state(state, "busy", "Конвейер занят другой погрузкой")
            return observation_error
        if observation_error:
            return True
        try:
            with transaction.atomic():
                sessions.lock_camera_binding()
                if not _binding_is_current(binding):
                    return False
                state.refresh_from_db()
                current = sessions.current_for_camera(binding.conveyor_camera)
                if current and current.status == AiCountingSession.ACTIVE:
                    _set_state(
                        state, "loading", "Идёт погрузка распознанного транспорта"
                    )
                    return False
                if _same_completed_visit(state):
                    _set_state(
                        state,
                        "completed",
                        "Погрузка завершена. Ожидаем подтверждённый выезд транспорта",
                    )
                    return False
                if not observation_is_current(state):
                    presence = state.tracking.get("presence")
                    detail = (
                        "Место погрузки свободно"
                        if presence == "absent"
                        else "Присутствие транспорта не подтверждено"
                        if presence == "unknown"
                        else "Ожидаем остановку транспорта"
                        if state.tracking.get("motion") != "stationary"
                        else "Ожидаем подтверждение номера стоящего транспорта"
                    )
                    _set_state(
                        state,
                        "confirming" if state.confirmations else "waiting_number",
                        detail,
                    )
                    return False
                if current and (
                    not current.automatically_started
                    or state.session_id != current.pk
                    or state.claimed_number != state.number
                    or state.claimed_visit_id != state.candidate_visit_id
                ):
                    _set_state(
                        state,
                        "busy",
                        "Ожидается восстановление уже закреплённой погрузки",
                    )
                    return False
                matches = matching_orders(binding, state.number)
                status = "multiple_orders" if len(matches) > 1 else "no_order"
                _record_evidence(binding, state, observation, status, owner=current)
                if len(matches) != 1:
                    _set_state(
                        state,
                        status,
                        "Подходит несколько заказов. Сведения сохранены"
                        if matches
                        else "Заказ не найден. Сведения сохранены",
                    )
                    return False
                _set_state(state, "starting")
            acquire(binding, state, matches[0])
            return False
        except (
            ai.AiError,
            ai.AiUnavailable,
            ValidationError,
            PermissionDenied,
            sessions.AiSessionBusy,
        ) as exc:
            state.refresh_from_db()
            _reset_votes(state)
            _set_state(
                state,
                "error",
                str(getattr(exc, "detail", None) or "Не удалось привязать заказ"),
            )
            return True


def _worker(binding_id):
    close_old_connections()
    try:
        return _poll_binding(binding_id)
    except Exception:
        log.exception("Shipping transport poll failed for binding=%s", binding_id)
        return True
    finally:
        close_old_connections()


def poll_once() -> dict:
    if not ai.enabled():
        return {"processed": 0, "errors": 0}
    ids = list(
        ShippingTransportCamera.objects.order_by("id").values_list("id", flat=True)
    )
    if not ids:
        return {"processed": 0, "errors": 0}
    with ThreadPoolExecutor(max_workers=min(4, len(ids))) as executor:
        errors = sum(executor.map(_worker, ids))
    return {"processed": len(ids), "errors": errors}
