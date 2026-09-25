from rest_framework.permissions import BasePermission, IsAuthenticated


def _auth(request):
    return bool(request.user and request.user.is_authenticated)


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and not request.user.is_client


class IsClientUser(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_client


class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and bool(request.user.is_superuser)


class DenyAll(BasePermission):
    def has_permission(self, request, view):
        return False


class HasPerm(BasePermission):
    """Любое из прав; с require_all=True — все сразу."""

    def __init__(self, *codes, require_all=False):
        self.codes = codes
        self.require_all = require_all

    def has_permission(self, request, view):
        user = request.user
        if not _auth(request) or user.is_client:
            return False
        check = all if self.require_all else any
        return check(user.has_perm_code(c) for c in self.codes)


# Значение в required_perms: действие доступно только суперпользователю.
SUPERUSER_ONLY = "__superuser__"


def _permissions_for(code):
    """Классы доступа для значения required_perms (код, кортеж кодов, SUPERUSER_ONLY)."""
    if code is None:
        return [DenyAll()]
    if code == SUPERUSER_ONLY:
        return [IsSuperUser()]
    codes = code if isinstance(code, (tuple, list)) else (code,)
    return [HasPerm(*codes)]


class PermViewSetMixin:
    required_perms: dict = {}

    def get_permissions(self):
        action = getattr(self, "action", None)
        if action is None:
            return [IsAuthenticated()]
        if action == "metadata":
            return [IsStaff()]
        return _permissions_for(self.required_perms.get(action))


class PermAPIViewMixin:
    required_perms = {}

    def get_permissions(self):
        method = self.request.method.lower()
        if not hasattr(self, method):
            # Метод view не обслуживает: DRF ответит 405, а не 403.
            return [IsAuthenticated()]
        if method in {"head", "options"}:
            method = "get"
        return _permissions_for(self.required_perms.get(method))
