from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.common.permissions import HasAllPerms, PermViewSetMixin
from apps.common.viewsets import SerializerViewSetMixin
from apps.eventlog.services import log_event

from .models import Employee
from .serializers import (
    EmployeeCreateUpdateSerializer,
    EmployeePasswordSerializer,
    EmployeeReadSerializer,
    EmployeeSecuritySerializer,
)


class EmployeeViewSet(
    SerializerViewSetMixin,
    PermViewSetMixin,
    viewsets.ModelViewSet,
):
    queryset = (
        Employee.objects.select_related("user", "sales_department")
        .prefetch_related("permissions")
    )
    serializer_class = EmployeeReadSerializer
    serializer_action_classes = {
        "create": EmployeeCreateUpdateSerializer,
        "update": EmployeeCreateUpdateSerializer,
        "partial_update": EmployeeCreateUpdateSerializer,
        "security": EmployeeSecuritySerializer,
        "set_password": EmployeePasswordSerializer,
    }
    required_perms = {
        "list": "employees.view",
        "retrieve": "employees.view",
        "create": "employees.manage",
        "update": "employees.manage",
        "partial_update": "employees.manage",
        "destroy": "employees.manage",
        "security": "employees.manage",
        "set_password": "employees.manage",
    }

    def get_permissions(self):
        if self.action in ("create", "destroy", "security", "set_password"):
            return [
                HasAllPerms("employees.manage", "sys_permissions.manage")
            ]
        return super().get_permissions()

    def _check_security_target(self, employee, *, forbid_self=False):
        actor = self.request.user
        if forbid_self and employee.user_id == actor.pk:
            raise PermissionDenied(
                {
                    "detail": "Нельзя отключить или изменить собственные системные права.",
                    "code": "self_security_change_forbidden",
                }
            )
        if actor.is_superuser:
            return
        if employee.user.is_superuser or employee.user.is_staff:
            raise PermissionDenied(
                {
                    "detail": (
                        "Только суперадминистратор может изменить "
                        "привилегированную учётную запись."
                    ),
                    "code": "staff_target_forbidden",
                }
            )

        target_codes = set(
            employee.permissions.values_list("code", flat=True)
        )
        excess = sorted(target_codes - actor.perm_codes)
        if excess:
            raise PermissionDenied(
                {
                    "detail": (
                        "Нельзя управлять учётной записью с более широкими правами: "
                        + ", ".join(excess)
                    ),
                    "code": "privileged_target_forbidden",
                }
            )

    @staticmethod
    def _security_snapshot(employee):
        return {
            "username": employee.user.username,
            "permission_codes": sorted(
                employee.permissions.values_list("code", flat=True)
            ),
            "sales_department_id": employee.sales_department_id,
            "is_active": employee.is_active,
        }

    @staticmethod
    def _profile_snapshot(employee):
        return {
            "first_name": employee.user.first_name,
            "last_name": employee.user.last_name,
            "phone": employee.phone,
            "position": employee.position,
        }

    @transaction.atomic
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

    @transaction.atomic
    def perform_update(self, serializer):
        before = self._profile_snapshot(serializer.instance)
        employee = serializer.save()
        after = self._profile_snapshot(employee)
        if before != after:
            log_event(
                "employee_profile",
                f"Изменён профиль сотрудника {employee.user.username}",
                user=self.request.user,
                payload={
                    "employee_id": employee.pk,
                    "before": before,
                    "after": after,
                },
            )

    @action(detail=True, methods=["patch"], url_path="security")
    @transaction.atomic
    def security(self, request, pk=None):
        employee = self.get_object()
        self._check_security_target(employee, forbid_self=True)
        before = self._security_snapshot(employee)
        serializer = self.get_serializer(employee, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        after = self._security_snapshot(employee)
        if before != after:
            log_event(
                "employee_security",
                f"Изменены доступы сотрудника {employee.user.username}",
                user=request.user,
                payload={
                    "employee_id": employee.pk,
                    "before": before,
                    "after": after,
                    "password_changed": False,
                },
            )
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="password")
    @transaction.atomic
    def set_password(self, request, pk=None):
        employee = self.get_object()
        self._check_security_target(employee)
        context = self.get_serializer_context()
        context["employee"] = employee
        serializer = self.get_serializer(
            data=request.data,
            context=context,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_event(
            "employee_security",
            f"Изменён пароль сотрудника {employee.user.username}",
            user=request.user,
            payload={
                "employee_id": employee.pk,
                "password_changed": True,
            },
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    def perform_destroy(self, instance):
        self._check_security_target(instance, forbid_self=True)
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
