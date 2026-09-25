"""Shared rules for permanent camera roles and analytics initialization."""

from rest_framework.exceptions import APIException, ValidationError

from apps.orders.models import Order
from apps.orders.statuses import CAMERA_BINDING_STATUSES
from apps.shipments.access import allowed_transports

from . import ai
from .models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AiCountingSession,
    ContinuousCameraRole,
    MonoblockCameraSettings,
)

# Моноблок только для просмотра: одно право видит всё. Режим AI 24/7, «Куда
# приходовать», повтор прихода и правки аналитики — только суперпользователь.
MONOBLOCK_VIEW = ("monoblock.view",)
# Настройки камер моноблока видит и администратор прав.
MONOBLOCK_SETTINGS_VIEW = ("monoblock.view", "sys_permissions.manage")

_CONTOUR_NAMES = {
    ANALYTICS_SCOPE_AI247: "AI 24/7",
    ANALYTICS_SCOPE_SHIPPING: "отгрузки",
}
_CONTOUR_CODES = {
    ANALYTICS_SCOPE_AI247: "camera_not_in_ai247",
    ANALYTICS_SCOPE_SHIPPING: "camera_not_in_shipping",
}


def assert_camera_has_no_active_work(camera: str) -> None:
    """Call under the shared camera-binding lock before changing its setup."""
    if AiCountingSession.objects.filter(
        camera=camera,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists() or Order.objects.filter(
        loading_camera=camera,
        status__in=CAMERA_BINDING_STATUSES,
    ).exists():
        raise ValidationError({
            "detail": "Сначала завершите активную отгрузку этой камеры",
            "code": "monoblock_busy",
        })


class CameraRoleImmutable(APIException):
    status_code = 409
    default_code = "camera_role_immutable"


def reserve_camera_roles(cameras, analytics_scope: str) -> None:
    """Create permanent contour ownership or reject an opposite assignment.

    Callers hold ``lock_camera_binding()`` inside one database transaction. The
    unique camera key remains a final guard if a non-HTTP writer races anyway.
    """

    cameras = sorted({camera for camera in cameras if isinstance(camera, str)})
    if not cameras:
        return
    existing = {
        row.camera: row.analytics_scope
        for row in ContinuousCameraRole.objects.select_for_update().filter(
            camera__in=cameras,
        )
    }
    conflicts = sorted(
        camera
        for camera, reserved_scope in existing.items()
        if reserved_scope != analytics_scope
    )
    if conflicts:
        _raise_role_immutable(conflicts, analytics_scope)

    missing = sorted(set(cameras) - set(existing))
    ContinuousCameraRole.objects.bulk_create(
        [
            ContinuousCameraRole(
                camera=camera,
                analytics_scope=analytics_scope,
            )
            for camera in missing
        ],
        ignore_conflicts=True,
    )
    raced_conflicts = sorted(
        ContinuousCameraRole.objects.filter(camera__in=missing)
        .exclude(analytics_scope=analytics_scope)
        .values_list("camera", flat=True)
    )
    if raced_conflicts:
        _raise_role_immutable(raced_conflicts, analytics_scope)


def _raise_role_immutable(cameras: list[str], analytics_scope: str) -> None:
    raise CameraRoleImmutable(
        {
            "message": (
                "Камера навсегда закреплена за другим контуром аналитики: "
                + ", ".join(cameras)
            ),
            "code": "camera_role_immutable",
            "cameras": cameras,
            "requested_analytics_scope": analytics_scope,
        }
    )


def assert_no_role_conflict(cameras, occupied, *, owner: str) -> None:
    """Reject cameras already active in the other contour of the monoblock."""

    conflicts = sorted(set(cameras) & set(occupied))
    if conflicts:
        raise ValidationError(
            {
                "camera_sources": (
                    "Одна камера может иметь только один контур подсчёта. "
                    f"Уже используется в {owner}: " + ", ".join(conflicts)
                ),
                "code": "camera_role_conflict",
                "cameras": conflicts,
            }
        )


def assert_always_on_capacity(
    effective_sources: list[str],
    previous_sources: list[str],
) -> None:
    """Reject an impossible policy when a trustworthy cached limit is known."""

    # A reduction (or a camera swap at the same cardinality) is how an
    # already-over-capacity installation recovers. Never block that path.
    if len(effective_sources) <= len(previous_sources):
        return
    live = ai.cached_always_on_status() if ai.enabled() else None
    capacity = (live or {}).get("capacity")
    if (
        type(capacity) is int
        and capacity >= 0
        and len(effective_sources) > capacity
    ):
        raise ValidationError(
            {
                "camera_sources": (
                    f"ПК камер поддерживает до {capacity} активных процессоров"
                ),
                "code": "always_on_capacity_exceeded",
            }
        )


def has_contour_role(camera: str, analytics_scope: str) -> bool:
    return ContinuousCameraRole.objects.filter(
        camera=camera,
        analytics_scope=analytics_scope,
    ).exists()


def assert_contour_camera(
    camera: str,
    analytics_scope: str,
    *,
    active: bool = False,
    field: str = "detail",
) -> str:
    """Return the normalized camera reserved for one analytics contour.

    The immutable reservation is the ownership authority; ``active`` also
    requires the camera in the contour's current list. Fail closed when a
    corrupted/manual DB edit makes them disagree so history of one contour
    cannot leak into the other contour's tools.
    """

    camera = ai.normalize(camera)
    name = _CONTOUR_NAMES[analytics_scope]
    code = _CONTOUR_CODES[analytics_scope]
    if active and camera not in MonoblockCameraSettings.contour_sources(
        analytics_scope
    ):
        raise ValidationError(
            {field: f"Камера не относится к контуру {name}", "code": code}
        )
    if not has_contour_role(camera, analytics_scope):
        raise ValidationError(
            {field: f"Камера не закреплена за контуром {name}", "code": code}
        )
    return camera


def session_started_by_name(session) -> str:
    if session.automatically_started:
        return "Автоматически"
    user = session.started_by
    if user is None:
        return "Система"
    return user.get_full_name() or user.username


def can_control_session(session, user) -> bool:
    """Whether a user may restart/stop a session they did not start.

    Грузчик управляет подсчётом только в своей области («Фуры | Вагоны») —
    по транспорту заказа сессии. Область проверяется и у администратора:
    сервисы подсчёта (``counting.start/stop``) требуют её у всех, и
    «Стоп» без неё был бы кнопкой с отказом. Суперпользователю открыты все
    области. Вызывающие в цикле берут сессии с ``select_related("order")``.
    """
    if not (user and user.is_authenticated):
        return False
    if session.order.transport_type not in allowed_transports(user):
        return False
    if user.has_perm_code("sys_permissions.manage"):
        return True
    return session.started_by_id == user.pk or (
        session.automatically_started
        and not user.is_client
        and user.has_perm_code("loader.confirm")
    )
