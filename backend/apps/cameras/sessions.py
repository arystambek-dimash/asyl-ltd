"""Order-bound lifecycle for the one global AI counting slot."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AiCountingSession


class AiSessionBusy(Exception):
    def __init__(self, session: AiCountingSession):
        self.session = session
        super().__init__(f"AI slot is owned by order {session.order_id}")


def current(*, lock: bool = False) -> AiCountingSession | None:
    qs = AiCountingSession.objects.filter(
        status__in=AiCountingSession.OPEN_STATUSES
    ).select_related("order")
    if lock:
        qs = qs.select_for_update()
    return qs.order_by("started_at").first()


def reserve(order, camera: str, user) -> tuple[AiCountingSession, bool]:
    """Atomically reserve the global slot, or return the same owner session."""
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
        # The unique partial index serializes simultaneous POSTs from tablets.
        session = current()
        if session and session.order_id == order.pk and session.camera == camera:
            return session, False
        if session:
            raise AiSessionBusy(session) from None
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
    session.error = ""
    session.save(update_fields=["status", "activated_at", "last_status", "error"])
    return session


def update_status(session: AiCountingSession, payload: dict) -> None:
    AiCountingSession.objects.filter(pk=session.pk).update(last_status=payload)


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

