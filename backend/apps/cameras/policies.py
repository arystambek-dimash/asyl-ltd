"""Small, shared authorization rules for camera-bound users and sessions."""

from rest_framework.exceptions import PermissionDenied


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
