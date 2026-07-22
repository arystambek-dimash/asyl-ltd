from rest_framework import viewsets
from apps.common.permissions import HasAllPerms, PermViewSetMixin
from apps.eventlog.services import log_event
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

    def get_permissions(self):
        if self.action in ("create", "destroy"):
            return [HasAllPerms("employees.manage", "rbac.manage")]
        return super().get_permissions()

    @staticmethod
    def _security_snapshot(employee):
        return {
            "username": employee.user.username,
            "role_id": employee.role_id,
            "sales_department_id": employee.sales_department_id,
            "permission_codes": sorted(
                employee.permissions.values_list("code", flat=True)
            ),
            "denied_permission_codes": sorted(
                employee.denied_permissions.values_list("code", flat=True)
            ),
            "is_active": employee.is_active,
        }

    def perform_create(self, serializer):
        employee = serializer.save()
        log_event(
            "employee_security",
            f"Создана учётная запись сотрудника {employee.user.username}",
            user=self.request.user,
            payload={
                "employee_id": employee.pk,
                "after": self._security_snapshot(employee),
            },
        )

    def perform_update(self, serializer):
        before = self._security_snapshot(serializer.instance)
        employee = serializer.save()
        after = self._security_snapshot(employee)
        if before != after or "password" in self.request.data:
            log_event(
                "employee_security",
                f"Изменены доступы сотрудника {employee.user.username}",
                user=self.request.user,
                payload={
                    "employee_id": employee.pk,
                    "before": before,
                    "after": after,
                    "password_changed": "password" in self.request.data,
                },
            )

    def perform_destroy(self, instance):
        # Удаление сотрудника не должно оставлять «живой» User с JWT-доступом.
        snapshot = self._security_snapshot(instance)
        employee_id = instance.pk
        user = instance.user
        instance.delete()
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])
        log_event(
            "employee_security",
            f"Деактивирована учётная запись сотрудника {user.username}",
            user=self.request.user,
            payload={"employee_id": employee_id, "before": snapshot},
        )
