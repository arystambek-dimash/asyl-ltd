"""Order-bound lifecycle for per-camera AI counting slots."""

from django.db import IntegrityError, transaction

from .models import AiCountingSession, MonoblockCameraSettings


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


def lock_camera_binding() -> None:
    """Serialize camera assignment changes with creation of AI sessions."""
    row, _ = MonoblockCameraSettings.objects.get_or_create(singleton=True)
    MonoblockCameraSettings.objects.select_for_update().get(pk=row.pk)


def reserve(
    order,
    camera: str,
    user,
) -> tuple[AiCountingSession, bool]:
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
