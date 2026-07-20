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


class HasPerm(BasePermission):
    """Право доступа: один код или несколько (достаточно любого из них)."""

    def __init__(self, *codes):
        self.codes = codes

    def has_permission(self, request, view):
        user = request.user
        if not _auth(request) or user.is_client:
            return False
        return any(user.has_perm_code(c) for c in self.codes)


class PermViewSetMixin:
    """Resolve required_perms[action] → HasPerm(code | (code, ...))."""
    required_perms: dict = {}

    def get_permissions(self):
        code = self.required_perms.get(getattr(self, "action", None))
        if code is None:
            return [IsAuthenticated()]
        codes = code if isinstance(code, (tuple, list)) else (code,)
        return [HasPerm(*codes)]
