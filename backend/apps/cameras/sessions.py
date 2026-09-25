"""Order-bound lifecycle for per-camera AI counting slots."""

from django.db import IntegrityError, transaction

from .models import AiCountingSession, MonoblockCameraSettings


class AiSessionBusy(Exception):
    def __init__(self, session: AiCountingSession):
        self.session = session
        super().__init__(
            f"Camera {session.camera} is owned by order {session.order_id}")


def _open_session(**lookup) -> AiCountingSession | None:
    return (
        AiCountingSession.objects.filter(
            status__in=AiCountingSession.OPEN_STATUSES, **lookup
        )
        .select_related("order")
        .order_by("started_at")
        .first()
    )


def current_for_camera(camera: str) -> AiCountingSession | None:
    """Open session (if any) on a specific camera."""
    return _open_session(camera=camera)


def current_for_order(order_id: int) -> AiCountingSession | None:
    """Open session for an order; an order cannot span multiple cameras."""
    return _open_session(order_id=order_id)


def lock_camera_binding() -> None:
    """Serialize camera assignment changes with creation of AI sessions."""
    row = MonoblockCameraSettings.load()
    MonoblockCameraSettings.objects.select_for_update().get(pk=row.pk)


def reserve(order, camera: str, user) -> tuple[AiCountingSession, bool]:
    """Atomically reserve a camera, or return the same owner session on it."""

    def _create() -> AiCountingSession:
        with transaction.atomic():
            return AiCountingSession.objects.create(
                order=order,
                camera=camera,
                status=AiCountingSession.STARTING,
                started_by=user,
            )

    try:
        return _create(), True
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
        # The conflicting session was closed between our INSERT and the
        # lookups above (closing does not take the binding lock); retry once.
        return _create(), True
