"""Small, shared authorization rules for camera-bound users and sessions."""

from rest_framework.exceptions import APIException, PermissionDenied

from .models import ContinuousCameraRole, ShippingAnalyticsBootstrap


class CameraRoleImmutable(APIException):
    status_code = 409
    default_code = "camera_role_immutable"


class ShippingBootstrapPending(APIException):
    status_code = 409
    default_code = "shipping_bootstrap_pending"


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
        raise CameraRoleImmutable(
            {
                "message": (
                    "Камера навсегда закреплена за другим контуром аналитики: "
                    + ", ".join(conflicts)
                ),
                "code": "camera_role_immutable",
                "cameras": conflicts,
                "requested_analytics_scope": analytics_scope,
            }
        )

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
        raise CameraRoleImmutable(
            {
                "message": (
                    "Камера навсегда закреплена за другим контуром аналитики: "
                    + ", ".join(raced_conflicts)
                ),
                "code": "camera_role_immutable",
                "cameras": raced_conflicts,
                "requested_analytics_scope": analytics_scope,
            }
        )


def assert_no_pending_shipping_bootstrap(cameras) -> None:
    """Keep initial shipping ownership stable until history is seeded."""

    cameras = sorted({camera for camera in cameras if isinstance(camera, str)})
    pending = sorted(
        ShippingAnalyticsBootstrap.objects.select_for_update()
        .filter(camera__in=cameras, completed_at__isnull=True)
        .values_list("camera", flat=True)
    )
    if pending:
        raise ShippingBootstrapPending(
            {
                "message": (
                    "История отгрузки ещё переносится; повторите после синхронизации: "
                    + ", ".join(pending)
                ),
                "code": "shipping_bootstrap_pending",
                "cameras": pending,
            }
        )


def active_device_for(user):
    return getattr(user, "active_monoblock_device", None)


def assert_device_camera(user, camera: str) -> None:
    """Keep a physical monoblock inside its assigned camera."""
    device = active_device_for(user)
    if device is not None and device.camera_source != camera:
        raise PermissionDenied("Эта камера закреплена за другим моноблоком")


def session_started_by_name(session) -> str:
    user = session.started_by
    if user is None:
        return "Система"
    return user.get_full_name() or user.username


def can_control_session(session, user) -> bool:
    """Whether a user may reset/stop a session they did not start."""
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_superuser
            or user.has_perm_code("sys_permissions.manage")
            or session.started_by_id == user.pk
        )
    )
