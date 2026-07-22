from rest_framework import viewsets
from apps.common.permissions import PermViewSetMixin
from .models import Employee
from .serializers import EmployeeSerializer


class EmployeeViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = (Employee.objects.select_related("user", "role", "sales_department")
                .prefetch_related("permissions", "denied_permissions", "role__permissions"))
    serializer_class = EmployeeSerializer
    required_perms = {
        "list": "employees.view", "retrieve": "employees.view",
        "create": "employees.manage", "update": "employees.manage",
        "partial_update": "employees.manage", "destroy": "employees.manage",
    }

    def perform_destroy(self, instance):
        # Удаление сотрудника не должно оставлять «живой» User с JWT-доступом.
        user = instance.user
        instance.delete()
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])
