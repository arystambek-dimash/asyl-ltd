"""Общие DRF-права для всех приложений — единственное место их определения."""
from rest_framework.permissions import BasePermission, IsAuthenticated


def _auth(request):
    return bool(request.user and request.user.is_authenticated)


class IsStaff(BasePermission):
    """Авторизованный сотрудник (не клиент портала)."""

    def has_permission(self, request, view):
        return _auth(request) and not request.user.is_client


class IsClientUser(BasePermission):
    """Авторизованный клиент портала."""

    def has_permission(self, request, view):
        return _auth(request) and request.user.is_client


class IsSuperUser(BasePermission):
    """Только системный суперпользователь, без наследования прав роли."""

    def has_permission(self, request, view):
        return _auth(request) and bool(request.user.is_superuser)


class DenyAll(BasePermission):
    """Fail-closed fallback for endpoints missing an explicit permission map."""

    def has_permission(self, request, view):
        return False


class HasPerm(BasePermission):
    """Право доступа: один код или несколько (достаточно любого из них)."""

    def __init__(self, *codes):
        self.codes = codes

    def has_permission(self, request, view):
        user = request.user
        if not _auth(request) or user.is_client:
            return False
        return any(user.has_perm_code(c) for c in self.codes)


class HasAllPerms(HasPerm):
    """Require every listed application permission (superusers still pass)."""

    def has_permission(self, request, view):
        user = request.user
        if not _auth(request) or user.is_client:
            return False
        return all(user.has_perm_code(code) for code in self.codes)


class PermViewSetMixin:
    """Resolve required_perms[action] → HasPerm(code | (code, ...))."""
    required_perms: dict = {}

    def get_permissions(self):
        action = getattr(self, "action", None)
        if action is None:
            # Let DRF return the protocol-correct 405 for unsupported methods.
            # A real routed action with no mapping still fails closed below.
            return [IsAuthenticated()]
        if action == "metadata":
            # OPTIONS exposes serializer metadata but no records. Keep browser
            # preflight/DRF metadata usable for authenticated staff only.
            return [IsStaff()]
        code = self.required_perms.get(action)
        if code is None:
            return [DenyAll()]
        codes = code if isinstance(code, (tuple, list)) else (code,)
        return [HasPerm(*codes)]
