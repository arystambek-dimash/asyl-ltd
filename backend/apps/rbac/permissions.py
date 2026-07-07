from rest_framework.permissions import BasePermission


class HasPerm(BasePermission):
    """Право доступа: один код или несколько (достаточно любого из них)."""

    def __init__(self, *codes):
        self.codes = codes

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_client", False):
            return False
        return any(user.has_perm_code(c) for c in self.codes)


class PermViewSetMixin:
    """Resolve required_perms[action] → HasPerm(code | (code, ...))."""
    required_perms: dict = {}

    def get_permissions(self):
        code = self.required_perms.get(getattr(self, "action", None))
        if code is None:
            from rest_framework.permissions import IsAuthenticated
            return [IsAuthenticated()]
        codes = code if isinstance(code, (tuple, list)) else (code,)
        return [HasPerm(*codes)]
