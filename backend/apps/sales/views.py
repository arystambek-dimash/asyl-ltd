from django.db.models import Count
from rest_framework import viewsets

from apps.common.permissions import HasPerm, IsStaff
from apps.orders.models import Order

from .access import scope_by_client_department
from .models import Department
from .serializers import DepartmentSerializer


class DepartmentViewSet(viewsets.ModelViewSet):
    """Динамические отделы продаж, используемые сотрудниками и заказами.

    Отдел не удаляется, а отключается через PATCH ``is_active``.
    """

    http_method_names = ["get", "post", "patch", "head", "options"]
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
        # Счётчик «N заказов» — как в отчётах: заказы из корзины не считаем.
        orders = scope_by_client_department(
            Order.objects.all(),
            self.request.user,
            client_path="client",
        )
        context["department_order_counts"] = dict(
            orders.values("department")
            .annotate(total=Count("id"))
            .values_list("department", "total")
        )
        return context
