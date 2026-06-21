from rest_framework import viewsets, mixins
from rest_framework.exceptions import ValidationError
from .models import Permission, Role
from .serializers import PermissionSerializer, RoleSerializer
from .permissions import PermViewSetMixin


class PermissionViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    required_perms = {"list": "employees.view"}


class RoleViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Role.objects.prefetch_related("permissions")
    serializer_class = RoleSerializer
    required_perms = {
        "list": "employees.view", "retrieve": "employees.view",
        "create": "employees.manage", "update": "employees.manage",
        "partial_update": "employees.manage", "destroy": "employees.manage",
    }

    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError({"detail": "Системную роль нельзя удалить", "code": "system_role"})
        if instance.employees.exists():
            raise ValidationError({"detail": "На роль назначены сотрудники — удаление запрещено", "code": "role_in_use"})
        instance.delete()
