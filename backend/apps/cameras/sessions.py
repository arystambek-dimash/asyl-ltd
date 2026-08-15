"""Order-bound lifecycle for per-camera AI counting slots."""

from django.conf import settings
from django.db import IntegrityError, transaction

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


def _observation_mode(camera: str, conveyor_transport: str) -> str:
    """Freeze which process supplies cloud-conveyor counter observations."""
    if conveyor_transport != AiCountingSession.CONVEYOR_CLOUD:
        return AiCountingSession.OBSERVATION_NONE
    legacy_cameras = getattr(settings, "CONVEYOR_LEGACY_BRIDGE_CAMERAS", ())
    if camera in legacy_cameras:
        return AiCountingSession.OBSERVATION_LEGACY_BRIDGE
    return AiCountingSession.OBSERVATION_EDGE


def reserve(
    order,
    camera: str,
    user,
    *,
    target_total: int = 0,
    conveyor_transport: str = AiCountingSession.CONVEYOR_NONE,
) -> tuple[AiCountingSession, bool]:
    """Atomically reserve a camera, or return the same owner session on it."""
    try:
        with transaction.atomic():
            session = AiCountingSession.objects.create(
                order=order,
                camera=camera,
                status=AiCountingSession.STARTING,
                started_by=user,
                target_total=target_total,
                conveyor_transport=conveyor_transport,
                conveyor_observation_mode=_observation_mode(
                    camera, conveyor_transport,
                ),
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
                target_total=target_total,
                conveyor_transport=conveyor_transport,
                conveyor_observation_mode=_observation_mode(
                    camera, conveyor_transport,
                ),
            )
        return session, True
