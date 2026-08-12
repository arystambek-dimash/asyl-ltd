from django.db.models import Count
from django.db.models.deletion import ProtectedError
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from apps.common.permissions import HasPerm, IsStaff
from apps.orders.models import Order

from .access import scope_by_client_department
from .models import Department
from .serializers import DepartmentSerializer


class DepartmentViewSet(viewsets.ModelViewSet):
    """Динамические отделы продаж, используемые сотрудниками и заказами."""

    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [HasPerm("sys_permissions.manage")]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list" and self.request.query_params.get("all") != "1":
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        orders = scope_by_client_department(
            Order.all_objects.all(),
            self.request.user,
            client_path="client",
        )
        context["department_order_counts"] = dict(
            orders.values("department")
            .annotate(total=Count("id"))
            .values_list("department", "total")
        )
        return context

    def perform_destroy(self, instance):
        if instance.clients.exists():
            raise ValidationError(
                {
                    "detail": (
                        "Отдел закреплён за клиентами. "
                        "Перенесите клиентов или отключите отдел."
                    ),
                    "code": "department_in_use",
                }
            )
        if Order.all_objects.filter(department=instance.code).exists():
            raise ValidationError(
                {
                    "detail": (
                        "Отдел используется в заказах. Отключите его вместо удаления."
                    ),
                    "code": "department_in_use",
                }
            )
        if instance.is_default:
            raise ValidationError(
                {
                    "detail": "Сначала назначьте другой основной отдел",
                    "code": "default_department",
                }
            )
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError(
                {"detail": "Отдел используется", "code": "department_in_use"}
            ) from exc
