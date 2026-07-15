from django.db.models import Count
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from apps.common.permissions import PermViewSetMixin

from .models import Permission, Role
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
    # Каталог прав нужен и тому, кто создаёт сотрудников (выбор доступов в форме).
    required_perms = {
        "list": (RBAC_VIEW, "employees.manage"),
        "retrieve": (RBAC_VIEW, "employees.manage"),
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
        # Удалять можно любые роли (включая системные), кроме тех, на которые
        # назначены сотрудники — иначе они остались бы без роли.
        if instance.employees.exists():
            raise ValidationError({"detail": "На роль назначены сотрудники — удаление запрещено", "code": "role_in_use"})
        instance.delete()
