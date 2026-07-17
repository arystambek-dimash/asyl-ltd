"""Order-bound lifecycle for per-camera AI counting slots."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AiCountingSession


class AiSessionBusy(Exception):
    def __init__(self, session: AiCountingSession):
        self.session = session
        super().__init__(
            f"Camera {session.camera} is owned by order {session.order_id}")


def current_for_camera(camera: str, *, lock: bool = False) -> AiCountingSession | None:
    """Open session (if any) on a specific camera."""
    qs = AiCountingSession.objects.filter(
        camera=camera, status__in=AiCountingSession.OPEN_STATUSES
    ).select_related("order")
    if lock:
        qs = qs.select_for_update()
    return qs.order_by("started_at").first()


def current_for_order(order_id: int, *, lock: bool = False) -> AiCountingSession | None:
    """Open session for an order; an order cannot span multiple cameras."""
    qs = AiCountingSession.objects.filter(
        order_id=order_id, status__in=AiCountingSession.OPEN_STATUSES
    ).select_related("order")
    if lock:
        qs = qs.select_for_update()
    return qs.order_by("started_at").first()


def reserve(order, camera: str, user) -> tuple[AiCountingSession, bool]:
    """Atomically reserve a camera, or return the same owner session on it."""
    try:
        with transaction.atomic():
            session = AiCountingSession.objects.create(
                order=order,
                camera=camera,
                status=AiCountingSession.STARTING,
                started_by=user,
            )
        return session, True
    except IntegrityError:
        # Partial indexes serialize simultaneous POSTs by both camera and order.
        session = current_for_camera(camera)
        if session and session.order_id == order.pk:
            return session, False
        if session:
            raise AiSessionBusy(session) from None
        order_session = current_for_order(order.pk)
        if order_session:
            raise AiSessionBusy(order_session) from None
        # Extremely small race with a transaction that rolled back; retry once.
        with transaction.atomic():
            session = AiCountingSession.objects.create(
                order=order,
                camera=camera,
                status=AiCountingSession.STARTING,
                started_by=user,
            )
        return session, True


def activate(session: AiCountingSession, payload: dict) -> AiCountingSession:
    session.status = AiCountingSession.ACTIVE
    session.activated_at = session.activated_at or timezone.now()
    session.last_status = payload
    stream = payload.get("stream")
    if isinstance(stream, str) and stream:
        session.recording_stream = stream[:64]
    session.error = ""
    session.save(update_fields=[
        "status", "activated_at", "recording_stream", "last_status", "error",
    ])
    return session


def update_status(session: AiCountingSession, payload: dict) -> None:
    updates = {"last_status": payload}
    stream = payload.get("stream")
    if isinstance(stream, str) and stream:
        updates["recording_stream"] = stream[:64]
    AiCountingSession.objects.filter(pk=session.pk).update(**updates)


def finish(session: AiCountingSession, user, payload: dict | None = None) -> None:
    payload = payload or {}
    AiCountingSession.objects.filter(pk=session.pk).update(
        status=AiCountingSession.CLOSED,
        closed_by=user,
        ended_at=timezone.now(),
        final_total=payload.get("total"),
        last_status=payload,
        error="",
    )


def fail(session: AiCountingSession, message: str) -> None:
    AiCountingSession.objects.filter(pk=session.pk).update(
        status=AiCountingSession.FAILED,
        ended_at=timezone.now(),
        error=message[:500],
    )
