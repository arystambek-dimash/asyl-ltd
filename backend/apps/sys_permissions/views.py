from apps.common.permissions import PermViewSetMixin
from rest_framework import mixins, viewsets

from .models import Permission
from .serializers import PermissionSerializer


class PermissionViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Permission.objects.order_by("section", "action", "code")
    serializer_class = PermissionSerializer
    http_method_names = ["get", "head", "options"]

    required_perms = {
        "list": (
            "sys_permissions.view",
            "employees.manage"
        ),
        "retrieve": (
            "sys_permissions.view",
            "employees.manage"
        ),
    }
