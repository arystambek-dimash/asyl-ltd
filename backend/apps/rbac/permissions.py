from rest_framework.permissions import BasePermission


class HasPerm(BasePermission):
    def __init__(self, code):
        self.code = code

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_client", False):
            return False
        return user.has_perm_code(self.code)


class PermViewSetMixin:
    """Resolve required_perms[action] → HasPerm(code)."""
    required_perms: dict = {}

    def get_permissions(self):
        code = self.required_perms.get(getattr(self, "action", None))
        if code is None:
            from rest_framework.permissions import IsAuthenticated
            return [IsAuthenticated()]
        return [HasPerm(code)]
