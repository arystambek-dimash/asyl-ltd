from rest_framework.permissions import BasePermission

from .models import ConveyorDevice


class IsConveyorDevice(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, ConveyorDevice)


class IsAiCallback(BasePermission):
    def has_permission(self, request, view):
        return request.auth == "camera-ai"
