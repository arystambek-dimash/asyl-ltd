from rest_framework import viewsets
from rbac.permissions import PermViewSetMixin
from .models import Employee
from .serializers import EmployeeSerializer


class EmployeeViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Employee.objects.select_related("user", "role")
    serializer_class = EmployeeSerializer
    required_perms = {
        "list": "employees.view", "retrieve": "employees.view",
        "create": "employees.manage", "update": "employees.manage",
        "partial_update": "employees.manage", "destroy": "employees.manage",
    }
