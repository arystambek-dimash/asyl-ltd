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
    def __init__(self, *codes):
        self.codes = codes

    def has_permission(self, request, view):
        user = request.user
        if not _auth(request) or user.is_client:
            return False
        return any(user.has_perm_code(c) for c in self.codes)


class HasAllPerms(HasPerm):

    def has_permission(self, request, view):
        user = request.user
        if not _auth(request) or user.is_client:
            return False
        return all(user.has_perm_code(code) for code in self.codes)


class PermViewSetMixin:
    required_perms: dict = {}

    def get_permissions(self):
        action = getattr(self, "action", None)
        if action is None:
            return [IsAuthenticated()]
        if action == "metadata":
            return [IsStaff()]
        code = self.required_perms.get(action)
        if code is None:
            return [DenyAll()]
        codes = code if isinstance(code, (tuple, list)) else (code,)
        return [HasPerm(*codes)]


class PermAPIViewMixin:
    required_perms = {}

    def get_permissions(self):
        method = self.request.method.lower()
        if method in {"head", "options"}:
            method = "get"
        codes = self.required_perms.get(method)

        if codes is None:
            return [DenyAll()]

        if isinstance(codes, str):
            codes = (codes,)

        return [HasPerm(*codes)]
