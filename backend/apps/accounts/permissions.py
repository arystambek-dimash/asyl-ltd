from rest_framework.permissions import BasePermission


def _auth(request):
    return bool(request.user and request.user.is_authenticated)


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and not request.user.is_client


class IsClientUser(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_client
