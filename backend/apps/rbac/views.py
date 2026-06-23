from django.db.models import Count
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from .models import Permission, Role
from .permissions import PermViewSetMixin
from .serializers import PermissionSerializer, RoleSerializer


RBAC_VIEW = "rbac.view"
RBAC_MANAGE = "rbac.manage"


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
        "list": RBAC_VIEW,
        "retrieve": RBAC_VIEW,
    }


class RoleViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = RoleSerializer
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]
    required_perms = {
        "list": RBAC_VIEW,
        "retrieve": RBAC_VIEW,
        "create": RBAC_MANAGE,
        "update": RBAC_MANAGE,
        "partial_update": RBAC_MANAGE,
        "destroy": RBAC_MANAGE,
    }

    def get_queryset(self):
        return (
            Role.objects.prefetch_related("permissions")
            .annotate(employee_count=Count("employees", distinct=True))
            .order_by("-is_system", "name")
        )

    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError({"detail": "Системную роль нельзя удалить", "code": "system_role"})
        if instance.employees.exists():
            raise ValidationError({"detail": "На роль назначены сотрудники — удаление запрещено", "code": "role_in_use"})
        instance.delete()
